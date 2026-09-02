from __future__ import annotations

import json
import os
from dataclasses import dataclass
from collections.abc import Iterator
from threading import Event
from typing import Any, Protocol

import httpx


class LLMUnavailable(RuntimeError):
    def __init__(self, message: str = "AI provider is unavailable", *,
                 code: str = "aiUnavailable", status_code: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class LLMClient(Protocol):
    def status(self) -> dict[str, Any]: ...

    def complete(self, messages: list[dict[str, Any]]) -> str: ...

    def stream(self, messages: list[dict[str, Any]],
               cancel_event: Event | None = None) -> Iterator[str]: ...


@dataclass
class DisabledLLM:
    reason: str = "AI provider is not configured"

    def status(self) -> dict[str, Any]:
        return {
            "configured": False,
            "provider": "openai-compatible",
            "model": None,
            "reason": self.reason,
        }

    def complete(self, messages: list[dict[str, Any]]) -> str:
        del messages
        raise LLMUnavailable(self.reason)

    def stream(self, messages: list[dict[str, Any]],
               cancel_event: Event | None = None) -> Iterator[str]:
        del messages, cancel_event
        raise LLMUnavailable(self.reason)


@dataclass
class OpenAICompatibleLLM:
    base_url: str
    model: str
    api_key: str = ""
    system_prompt: str = (
        "You are the AI assistant inside WeavePath. "
        "Use only the supplied route-specific conversation history and reply in the user's language."
    )
    timeout_seconds: float = 60.0

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "provider": "openai-compatible",
            "model": self.model,
            "reason": None,
        }

    def complete(self, messages: list[dict[str, Any]]) -> str:
        payload_messages = [{"role": "system", "content": self.system_prompt}]
        payload_messages.extend(
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item.get("role") in {"system", "user", "assistant"} and item.get("content")
        )
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self.base_url.rstrip("/") + "/chat/completions",
                    headers=headers,
                    json={"model": self.model, "messages": payload_messages},
                )
                response.raise_for_status()
                data = response.json()
            content = data["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(
                "AI provider request timed out", code="aiTimeout", status_code=504
            ) from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMUnavailable("AI provider is unavailable") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailable(
                "AI provider returned an empty response", code="aiEmptyResponse", status_code=502
            )
        return content.strip()

    def stream(self, messages: list[dict[str, Any]],
               cancel_event: Event | None = None) -> Iterator[str]:
        """Yield OpenAI-compatible chat completion deltas.

        Providers that implement the OpenAI streaming shape send one JSON
        object per ``data:`` SSE line and terminate with ``[DONE]``.  A few
        local gateways return newline-delimited JSON instead, so both forms
        are accepted.  No partial answer is persisted by the API layer when
        cancellation or an upstream error occurs.
        """
        payload_messages = [{"role": "system", "content": self.system_prompt}]
        payload_messages.extend(
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item.get("role") in {"system", "user", "assistant"} and item.get("content")
        )
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                with client.stream(
                    "POST",
                    self.base_url.rstrip("/") + "/chat/completions",
                    headers=headers,
                    json={"model": self.model, "messages": payload_messages, "stream": True},
                ) as response:
                    response.raise_for_status()
                    for raw_line in response.iter_lines():
                        if cancel_event is not None and cancel_event.is_set():
                            return
                        line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else raw_line
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            return
                        try:
                            data = json.loads(line)
                        except (TypeError, ValueError):
                            # Ignore comments/unknown gateway keep-alives.
                            continue
                        choices = data.get("choices") if isinstance(data, dict) else None
                        if not isinstance(choices, list) or not choices:
                            continue
                        choice = choices[0] if isinstance(choices[0], dict) else {}
                        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                        content = delta.get("content")
                        if content is None and isinstance(choice.get("text"), str):
                            content = choice["text"]
                        if isinstance(content, str) and content:
                            yield content
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(
                "AI provider request timed out", code="aiTimeout", status_code=504
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise LLMUnavailable("AI provider is unavailable") from exc


def build_llm_from_env() -> LLMClient:
    base_url = (os.getenv("WEAVEPATH_LLM_BASE_URL")
                or os.getenv("COTHINKER_LLM_BASE_URL") or "").strip()
    model = (os.getenv("WEAVEPATH_LLM_MODEL")
             or os.getenv("COTHINKER_LLM_MODEL") or "").strip()
    api_key = (os.getenv("WEAVEPATH_LLM_API_KEY")
               or os.getenv("COTHINKER_LLM_API_KEY")
               or os.getenv("OPENAI_API_KEY") or "").strip()
    if not base_url and api_key:
        base_url = "https://api.openai.com/v1"
    if not base_url or not model:
        return DisabledLLM("Set WEAVEPATH_LLM_BASE_URL and WEAVEPATH_LLM_MODEL to enable AI replies")
    try:
        timeout = float(os.getenv("WEAVEPATH_LLM_TIMEOUT")
                        or os.getenv("COTHINKER_LLM_TIMEOUT") or "60")
    except ValueError:
        timeout = 60.0
    return OpenAICompatibleLLM(
        base_url=base_url,
        model=model,
        api_key=api_key,
        system_prompt=(os.getenv("WEAVEPATH_LLM_SYSTEM_PROMPT")
                       or os.getenv("COTHINKER_LLM_SYSTEM_PROMPT") or "").strip()
        or OpenAICompatibleLLM.__dataclass_fields__["system_prompt"].default,
        timeout_seconds=max(1.0, timeout),
    )
