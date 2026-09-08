from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx

from api.llm import LLMUnavailable, OpenAICompatibleLLM


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


@dataclass(frozen=True)
class ModelTurn:
    final_answer: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_call_id: str | None = None
    # Normalized provider usage. ``None`` means the provider did not report
    # usage; unknown values must never be represented as zero.
    usage: dict[str, int | str | None] | None = None


class ModelProtocolError(ValueError):
    """A completed provider response that cannot be accepted as a model turn.

    Providers may still report billable/cache usage on a truncated, filtered,
    empty, or otherwise malformed HTTP 200 response. Carry the normalized
    allowlist fields so Runtime can journal the real call before failing it.
    """

    def __init__(self, message: str,
                 usage: dict[str, int | str | None] | None = None) -> None:
        super().__init__(message)
        self.usage = usage


def _usage(response_body: dict[str, Any]) -> dict[str, int | str | None] | None:
    raw = response_body.get("usage")
    if not isinstance(raw, dict):
        return None

    def integer(*keys: str) -> int | None:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    input_tokens = integer("prompt_tokens", "input_tokens")
    output_tokens = integer("completion_tokens", "output_tokens")
    cached_tokens: int | None = None
    cache_fields_present = False
    cache_invalid = False
    nested_cached_values: list[int] = []
    for detail_key in ("prompt_tokens_details", "input_tokens_details"):
        details = raw.get(detail_key)
        if isinstance(details, dict):
            if "cached_tokens" in details:
                cache_fields_present = True
                value = details.get("cached_tokens")
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    nested_cached_values.append(value)
                else:
                    cache_invalid = True
    if nested_cached_values:
        cached_tokens = nested_cached_values[0]
        if any(value != cached_tokens for value in nested_cached_values[1:]):
            cache_invalid = True
    # DeepSeek exposes cache accounting as top-level usage fields.
    cache_hit = integer("prompt_cache_hit_tokens")
    cache_miss = integer("prompt_cache_miss_tokens")
    for cache_key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        if cache_key in raw:
            cache_fields_present = True
            value = raw[cache_key]
            if not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
                cache_invalid = True
    if cached_tokens is not None and cache_hit is not None and cached_tokens != cache_hit:
        cache_invalid = True
    if cached_tokens is None:
        cached_tokens = cache_hit
    uncached_tokens = cache_miss
    if input_tokens is None and cache_hit is not None and cache_miss is not None:
        input_tokens = cache_hit + cache_miss
    if uncached_tokens is None and input_tokens is not None and cached_tokens is not None:
        uncached_tokens = max(0, input_tokens - cached_tokens)
    if (input_tokens is not None and cached_tokens is not None
            and cached_tokens > input_tokens):
        cache_invalid = True
    if (input_tokens is not None and cache_hit is not None and cache_miss is not None
            and cache_hit + cache_miss != input_tokens):
        cache_invalid = True
    if cache_invalid:
        cached_tokens = uncached_tokens = None
        cache_status = "invalid"
    elif cache_fields_present:
        cache_status = "reported"
    elif input_tokens is not None or output_tokens is not None:
        cache_status = "unsupported"
    else:
        cache_status = "not_reported"
    if (cache_status == "not_reported" and all(value is None for value in (
            input_tokens, output_tokens, cached_tokens, uncached_tokens))):
        return None
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cachedInputTokens": cached_tokens,
        "uncachedInputTokens": uncached_tokens,
        "cacheStatus": cache_status,
    }


class AgentModelPort(Protocol):
    def bind(self) -> "AgentModelPort": ...
    def snapshot(self) -> dict[str, Any]: ...
    def next(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn: ...


class ScriptedMockAgentAdapter:
    def __init__(self, turns: list[ModelTurn], on_turn: Callable[[int], None] | None = None) -> None:
        self.turns, self.on_turn, self.calls = list(turns), on_turn, 0

    def snapshot(self) -> dict[str, Any]:
        return {"provider": "scripted-mock", "model": "deterministic-v1"}

    def bind(self) -> "ScriptedMockAgentAdapter":
        return self

    def next(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        del messages, tools
        index = self.calls
        self.calls += 1
        if self.on_turn:
            self.on_turn(index)
        if index >= len(self.turns):
            raise ValueError("script exhausted")
        return self.turns[index]


class OpenAICompatibleAgentAdapter:
    def __init__(self, client_factory: Callable[[], OpenAICompatibleLLM]) -> None:
        self.client_factory = client_factory

    def bind(self) -> "OpenAICompatibleAgentAdapter":
        client = self.client_factory()
        return OpenAICompatibleAgentAdapter(lambda: client)

    def snapshot(self) -> dict[str, Any]:
        client = self.client_factory()
        return {"provider": "openai-compatible", "baseUrl": client.base_url, "model": client.model,
                "timeoutSeconds": client.timeout_seconds, "systemPrompt": client.system_prompt,
                "adapterVersion": "1.0.0"}

    def next(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        client = self.client_factory()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if client.api_key:
            headers["Authorization"] = f"Bearer {client.api_key}"
        payload_messages: list[dict[str, Any]] = []
        # Runtime prompt builders provide the policy as the first system
        # message. Keep the legacy direct-adapter behavior for callers that do
        # not use a builder, without prepending ahead of a cache-aware policy.
        if (not messages or messages[0].get("role") != "system") and client.system_prompt:
            payload_messages.append({"role": "system", "content": client.system_prompt})
        for item in messages:
            if "toolCall" in item:
                call = item["toolCall"]
                payload_messages.append({"role": "assistant", "content": None, "tool_calls": [{
                    "id": call["id"], "type": "function", "function": {"name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False)}}]})
            elif item.get("role") == "tool":
                payload_messages.append({"role": "tool", "tool_call_id": item["toolCallId"],
                                         "content": item["content"]})
            else:
                payload_messages.append({"role": item["role"], "content": item["content"]})
        payload_tools = [{"type": "function", "function": {"name": t["name"],
                          "description": f"{t['description']} Version: {t['version']}.",
                          "parameters": t["schema"]}} for t in tools]
        normalized_usage: dict[str, int | str | None] | None = None
        try:
            with httpx.Client(timeout=client.timeout_seconds) as http:
                response = http.post(client.base_url.rstrip("/") + "/chat/completions", headers=headers,
                                     json={"model": client.model, "messages": payload_messages,
                                           "tools": payload_tools, "parallel_tool_calls": False})
                response.raise_for_status()
                response_body = response.json()
                if not isinstance(response_body, dict):
                    raise TypeError("model response must be an object")
                normalized_usage = _usage(response_body)
                choice = response_body["choices"][0]
                if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
                    raise ValueError("model returned an invalid choice")
                message = choice["message"]
                finish_reason = choice.get("finish_reason")
            if finish_reason in {"length", "content_filter"}:
                raise ValueError("model response was truncated or filtered")
            calls = message.get("tool_calls")
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise ValueError("model returned invalid tool calls")
            if calls:
                if finish_reason not in {None, "tool_calls"} or len(calls) != 1:
                    raise ValueError("model returned an invalid number of tool calls")
                call = calls[0]
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    raise ValueError("model returned an invalid tool call")
                if call.get("type", "function") != "function":
                    raise ValueError("model returned an unsupported tool call")
                function = call["function"]
                arguments = json.loads(
                    function["arguments"], parse_constant=_reject_non_finite_json
                )
                if not isinstance(arguments, dict):
                    raise ValueError("model returned invalid tool arguments")
                return ModelTurn(tool_name=function["name"],
                                 tool_arguments=arguments,
                                 tool_call_id=call.get("id"), usage=normalized_usage)
            if finish_reason not in {None, "stop"}:
                raise ValueError("model returned an unsupported finish reason")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model turn")
            return ModelTurn(final_answer=content.strip(), usage=normalized_usage)
        except httpx.TimeoutException as exc:
            raise LLMUnavailable("AI provider request timed out", code="aiTimeout", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable("AI provider is unavailable") from exc
        except ModelProtocolError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelProtocolError(
                "invalid model protocol response", normalized_usage
            ) from exc
