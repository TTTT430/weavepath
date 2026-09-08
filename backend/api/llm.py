from __future__ import annotations

import json
import os
from dataclasses import dataclass
from collections.abc import Iterator
from threading import Event
from time import sleep
from typing import Any, Protocol

import httpx


CONNECT_RETRY_ATTEMPTS = 3
CONNECT_RETRY_DELAYS = (0.25, 1.0)
RETRYABLE_PROVIDER_STATUSES = {408, 425, 429, 500, 502, 503, 504}


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
    # This is a connection/write timeout, not a model-generation timeout.
    # Once the provider accepts the request, reads may continue indefinitely
    # until completion or explicit cancellation.
    timeout_seconds: float = 15.0

    def request_timeout(self) -> httpx.Timeout:
        timeout = max(1.0, min(float(self.timeout_seconds), 60.0))
        return httpx.Timeout(connect=timeout, read=None, write=max(timeout, 30.0), pool=timeout)

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in RETRYABLE_PROVIDER_STATUSES
        return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError,
                                httpx.RemoteProtocolError, OSError))

    @staticmethod
    def _transport_error(exc: BaseException) -> LLMUnavailable:
        if OpenAICompatibleLLM._retryable(exc):
            return LLMUnavailable(
                f"Unable to connect to the AI provider after {CONNECT_RETRY_ATTEMPTS} attempts",
                code="aiConnectionFailed", status_code=503,
            )
        return LLMUnavailable("AI provider is unavailable")

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
        headers = self._headers("application/json")
        data: dict[str, Any] | None = None
        for attempt in range(1, CONNECT_RETRY_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=self.request_timeout()) as client:
                    response = client.post(
                        self.base_url.rstrip("/") + "/chat/completions",
                        headers=headers,
                        json={"model": self.model, "messages": payload_messages},
                    )
                    response.raise_for_status()
                    value = response.json()
                    if not isinstance(value, dict):
                        raise TypeError("model response must be an object")
                    data = value
                break
            except (httpx.HTTPError, OSError) as exc:
                if self._retryable(exc) and attempt < CONNECT_RETRY_ATTEMPTS:
                    sleep(CONNECT_RETRY_DELAYS[attempt - 1])
                    continue
                raise self._transport_error(exc) from exc
            except (TypeError, ValueError) as exc:
                raise LLMUnavailable("AI provider is unavailable") from exc
        try:
            assert data is not None
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, AssertionError) as exc:
            raise LLMUnavailable("AI provider is unavailable") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailable(
                "AI provider returned an empty response", code="aiEmptyResponse", status_code=502
            )
        return content.strip()

    def stream(self, messages: list[dict[str, Any]],
               cancel_event: Event | None = None) -> Iterator[str]:
        for item in self.stream_events(messages, cancel_event):
            if item.get("type") == "delta" and isinstance(item.get("content"), str):
                yield item["content"]

    def stream_events(self, messages: list[dict[str, Any]],
                      cancel_event: Event | None = None) -> Iterator[dict[str, Any]]:
        """Yield connection lifecycle events and chat completion deltas.

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
        headers = self._headers("text/event-stream")
        for attempt in range(1, CONNECT_RETRY_ATTEMPTS + 1):
            if cancel_event is not None and cancel_event.is_set():
                return
            if attempt == 1:
                yield {"type": "status", "phase": "connecting", "attempt": attempt,
                       "maxAttempts": CONNECT_RETRY_ATTEMPTS}
            received_content = False
            try:
                with httpx.Client(timeout=self.request_timeout()) as client:
                    with client.stream(
                        "POST",
                        self.base_url.rstrip("/") + "/chat/completions",
                        headers=headers,
                        json={"model": self.model, "messages": payload_messages, "stream": True},
                    ) as response:
                        response.raise_for_status()
                        yield {"type": "status", "phase": "waiting", "attempt": attempt,
                               "maxAttempts": CONNECT_RETRY_ATTEMPTS}
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
                                if not received_content:
                                    yield {"type": "status", "phase": "receiving", "attempt": attempt,
                                           "maxAttempts": CONNECT_RETRY_ATTEMPTS}
                                received_content = True
                                yield {"type": "delta", "content": content}
                return
            except (httpx.HTTPError, OSError) as exc:
                if self._retryable(exc) and attempt < CONNECT_RETRY_ATTEMPTS:
                    delay = CONNECT_RETRY_DELAYS[attempt - 1]
                    # An OpenAI-compatible stream cannot resume from a token
                    # offset. Tell consumers to discard the partial draft,
                    # then safely restart the request from the same context.
                    if received_content:
                        yield {"type": "reset"}
                    yield {"type": "status", "phase": "reconnecting", "attempt": attempt + 1,
                           "maxAttempts": CONNECT_RETRY_ATTEMPTS, "delayMs": round(delay * 1000)}
                    if cancel_event is not None:
                        if cancel_event.wait(delay):
                            return
                    else:
                        sleep(delay)
                    continue
                raise self._transport_error(exc) from exc


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
        timeout = float(os.getenv("WEAVEPATH_LLM_CONNECT_TIMEOUT")
                        or os.getenv("WEAVEPATH_LLM_TIMEOUT")
                        or os.getenv("COTHINKER_LLM_TIMEOUT") or "15")
    except ValueError:
        timeout = 15.0
    return OpenAICompatibleLLM(
        base_url=base_url,
        model=model,
        api_key=api_key,
        system_prompt=(os.getenv("WEAVEPATH_LLM_SYSTEM_PROMPT")
                       or os.getenv("COTHINKER_LLM_SYSTEM_PROMPT") or "").strip()
        or OpenAICompatibleLLM.__dataclass_fields__["system_prompt"].default,
        timeout_seconds=max(1.0, timeout),
    )
