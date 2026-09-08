from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.llm import LLMUnavailable, OpenAICompatibleLLM, build_llm_from_env
from graph_core import GraphStore


class FakeClient:
    behavior = "timeout"
    calls = 0
    routes = []

    def __init__(self, **kwargs):
        type(self).routes.append(kwargs.get("trust_env"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, **_kwargs):
        type(self).calls += 1
        request = httpx.Request("POST", url)
        if self.behavior == "timeout":
            raise httpx.ReadTimeout("upstream secret timeout detail", request=request)
        if self.behavior == "unavailable":
            return httpx.Response(
                401,
                text="provider body contains api-key-should-never-leak",
                request=request,
            )
        if self.behavior == "unsupported_reasoning":
            return httpx.Response(
                400,
                text="unsupported parameter: reasoning_effort",
                request=request,
            )
        if self.behavior == "context_too_large":
            return httpx.Response(
                400,
                text="maximum context length exceeded",
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "   "}}]},
            request=request,
        )


class FakeStreamResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
        yield "data: [DONE]"


class ReconnectingStreamClient:
    calls = 0
    routes = []

    def __init__(self, **kwargs):
        type(self).routes.append(kwargs.get("trust_env"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def stream(self, _method, url, **_kwargs):
        type(self).calls += 1
        if self.calls == 1:
            raise httpx.ConnectError("temporary disconnect", request=httpx.Request("POST", url))
        return FakeStreamResponse()


class PartialThenReconnectingResponse(FakeStreamResponse):
    def __init__(self, attempt):
        self.attempt = attempt

    def iter_lines(self):
        if self.attempt == 1:
            yield 'data: {"choices":[{"delta":{"content":"discard me"}}]}'
            raise httpx.ReadError(
                "stream disconnected",
                request=httpx.Request("POST", "https://provider.test/v1/chat/completions"),
            )
        yield 'data: {"choices":[{"delta":{"content":"final"}}]}'
        yield "data: [DONE]"


class PartialThenReconnectingClient(ReconnectingStreamClient):
    def stream(self, _method, _url, **_kwargs):
        type(self).calls += 1
        return PartialThenReconnectingResponse(self.calls)


class UsageStreamResponse(FakeStreamResponse):
    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
        yield ('data: {"choices":[],"usage":{"prompt_tokens":100,'
               '"completion_tokens":4,"prompt_cache_hit_tokens":80,'
               '"prompt_cache_miss_tokens":20}}')
        yield "data: [DONE]"


class UsageStreamClient(ReconnectingStreamClient):
    request_json = None

    def stream(self, _method, _url, **kwargs):
        type(self).request_json = kwargs.get("json")
        return UsageStreamResponse()


class UnsupportedUsageResponse(FakeStreamResponse):
    def __init__(self, supported):
        self.supported = supported

    def raise_for_status(self):
        if not self.supported:
            request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
            raise httpx.HTTPStatusError(
                "unsupported stream_options",
                request=request,
                response=httpx.Response(400, request=request),
            )


class UnsupportedUsageClient(ReconnectingStreamClient):
    request_jsons = []

    def stream(self, _method, _url, **kwargs):
        payload = kwargs.get("json")
        type(self).request_jsons.append(payload)
        return UnsupportedUsageResponse("stream_options" not in payload)


def test_build_llm_prefers_weavepath_environment_and_keeps_legacy_fallback(monkeypatch):
    values = {
        "WEAVEPATH_LLM_BASE_URL": "https://weavepath.test/v1",
        "WEAVEPATH_LLM_MODEL": "new-model",
        "WEAVEPATH_LLM_API_KEY": "new-key",
        "WEAVEPATH_LLM_TIMEOUT": "17",
        "WEAVEPATH_LLM_SYSTEM_PROMPT": "new prompt",
        "WEAVEPATH_LLM_REASONING_EFFORT": "high",
        "COTHINKER_LLM_BASE_URL": "https://legacy.test/v1",
        "COTHINKER_LLM_MODEL": "legacy-model",
        "COTHINKER_LLM_API_KEY": "legacy-key",
        "COTHINKER_LLM_TIMEOUT": "31",
        "COTHINKER_LLM_SYSTEM_PROMPT": "legacy prompt",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    client = build_llm_from_env()
    assert isinstance(client, OpenAICompatibleLLM)
    assert (client.base_url, client.model, client.api_key) == (
        "https://weavepath.test/v1", "new-model", "new-key"
    )
    assert client.timeout_seconds == 17
    timeout = client.request_timeout()
    assert timeout.connect == 17
    assert timeout.read is None
    assert client.system_prompt == "new prompt"
    assert client.reasoning_effort == "high"

    for key in [name for name in values if name.startswith("WEAVEPATH_")]:
        monkeypatch.delenv(key)
    legacy = build_llm_from_env()
    assert isinstance(legacy, OpenAICompatibleLLM)
    assert (legacy.base_url, legacy.model, legacy.api_key) == (
        "https://legacy.test/v1", "legacy-model", "legacy-key"
    )


def test_stream_reports_connection_phases_and_reconnects_before_content(monkeypatch):
    ReconnectingStreamClient.calls = 0
    ReconnectingStreamClient.routes = []
    monkeypatch.setattr("api.llm.httpx.Client", ReconnectingStreamClient)
    monkeypatch.setattr("api.llm.CONNECT_RETRY_DELAYS", (0.0, 0.0))
    client = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a")
    events = list(client.stream_events([{"role": "user", "content": "question"}]))
    assert [event["phase"] for event in events if event["type"] == "status"] == [
        "connecting", "reconnecting", "waiting", "receiving"
    ]
    assert [event["content"] for event in events if event["type"] == "delta"] == ["ok"]
    assert ReconnectingStreamClient.calls == 2
    assert ReconnectingStreamClient.routes == [False, True]


def test_stream_resets_partial_draft_before_reconnecting(monkeypatch):
    PartialThenReconnectingClient.calls = 0
    PartialThenReconnectingClient.routes = []
    monkeypatch.setattr("api.llm.httpx.Client", PartialThenReconnectingClient)
    monkeypatch.setattr("api.llm.CONNECT_RETRY_DELAYS", (0.0, 0.0))
    client = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a")
    events = list(client.stream_events([{"role": "user", "content": "question"}]))
    assert [event["type"] for event in events] == [
        "status", "status", "status", "delta", "reset", "status", "status", "status", "delta"
    ]
    reset_index = next(index for index, event in enumerate(events) if event["type"] == "reset")
    assert events[reset_index + 1]["phase"] == "reconnecting"
    assert PartialThenReconnectingClient.calls == 2
    # A response stream was already established, so retry on the same route;
    # automatic proxy fallback is reserved for connection establishment.
    assert PartialThenReconnectingClient.routes == [False, False]


def test_stream_requests_and_normalizes_provider_cache_usage(monkeypatch):
    monkeypatch.setattr("api.llm.httpx.Client", UsageStreamClient)
    client = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a",
                                 reasoning_effort="high")
    events = list(client.stream_events([{"role": "user", "content": "question"}]))
    assert UsageStreamClient.request_json["stream_options"] == {"include_usage": True}
    assert UsageStreamClient.request_json["reasoning_effort"] == "high"
    assert next(event["usage"] for event in events if event["type"] == "usage") == {
        "inputTokens": 100,
        "outputTokens": 4,
        "cachedInputTokens": 80,
        "uncachedInputTokens": 20,
        "cacheStatus": "reported",
    }


def test_stream_falls_back_when_gateway_rejects_optional_usage_flag(monkeypatch):
    UnsupportedUsageClient.request_jsons = []
    monkeypatch.setattr("api.llm.httpx.Client", UnsupportedUsageClient)
    client = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a")
    events = list(client.stream_events([{"role": "user", "content": "question"}]))
    assert [item["content"] for item in events if item["type"] == "delta"] == ["ok"]
    assert len(UnsupportedUsageClient.request_jsons) == 2
    assert "stream_options" in UnsupportedUsageClient.request_jsons[0]
    assert "stream_options" not in UnsupportedUsageClient.request_jsons[1]


def test_reasoning_effort_rejection_has_an_actionable_safe_error(monkeypatch):
    FakeClient.behavior = "unsupported_reasoning"
    FakeClient.calls = 0
    monkeypatch.setattr("api.llm.httpx.Client", FakeClient)
    client = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a",
                                 reasoning_effort="xhigh")
    with pytest.raises(LLMUnavailable) as caught:
        client.complete([{"role": "user", "content": "question"}])
    assert caught.value.code == "reasoningEffortUnsupported"
    assert caught.value.status_code == 422
    assert "unsupported parameter" not in str(caught.value)


def test_provider_context_limit_has_an_actionable_safe_error(monkeypatch):
    FakeClient.behavior = "context_too_large"
    FakeClient.calls = 0
    monkeypatch.setattr("api.llm.httpx.Client", FakeClient)
    client = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a",
                                 reasoning_effort="high")
    with pytest.raises(LLMUnavailable) as caught:
        client.complete([{"role": "user", "content": "large request"}])
    assert caught.value.code == "aiContextTooLarge"
    assert caught.value.status_code == 422
    assert "maximum context length" not in str(caught.value)


@pytest.mark.parametrize(
    ("behavior", "status", "code", "message"),
    [
        ("timeout", 503, "aiConnectionFailed", "Unable to connect to the AI provider after 3 attempts"),
        ("unavailable", 503, "aiUnavailable", "AI provider is unavailable"),
        ("empty", 502, "aiEmptyResponse", "AI provider returned an empty response"),
    ],
)
def test_chat_has_stable_safe_llm_error_protocol(
    tmp_path, monkeypatch, behavior, status, code, message
):
    FakeClient.behavior = behavior
    FakeClient.calls = 0
    FakeClient.routes = []
    monkeypatch.setattr("api.llm.httpx.Client", FakeClient)
    store = GraphStore(":memory:")
    llm = OpenAICompatibleLLM(
        base_url="https://provider.test/v1",
        model="model-a",
        api_key="api-key-should-never-leak",
    )
    with TestClient(create_app(store, llm_client=llm)) as client:
        graph = client.post(
            "/api/v1/workflows",
            json={"name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"},
        ).json()
        workflow_id = graph["workflowId"]
        response = client.post(
            f"/api/v1/workflows/{workflow_id}/instances/A/chat",
            json={"content": "question"},
        )
        assert response.status_code == status
        assert response.json() == {"code": code, "error": message}
        assert "api-key-should-never-leak" not in response.text
        assert "upstream secret" not in response.text
        messages = client.get(
            f"/api/v1/workflows/{workflow_id}/instances/A/messages"
        ).json()["messages"]
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "question")
        ]
        assert FakeClient.calls == (3 if behavior == "timeout" else 1)
        if behavior == "timeout":
            assert FakeClient.routes == [False, False, False]
    store.close()


@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_chat_rejects_blank_content_before_storage_or_llm_call(content):
    store = GraphStore(":memory:")
    llm = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a")
    with TestClient(create_app(store, llm_client=llm)) as client:
        graph = client.post(
            "/api/v1/workflows",
            json={"name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"},
        ).json()
        workflow_id = graph["workflowId"]
        response = client.post(
            f"/api/v1/workflows/{workflow_id}/instances/A/chat", json={"content": content}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validationError"
        assert client.get(
            f"/api/v1/workflows/{workflow_id}/instances/A/messages"
        ).json()["messages"] == []
    store.close()


def test_chat_rejects_content_over_limit_without_echoing_it():
    store = GraphStore(":memory:")
    llm = OpenAICompatibleLLM(base_url="https://provider.test/v1", model="model-a")
    secret_content = "s" * 4_000_001
    with TestClient(create_app(store, llm_client=llm)) as client:
        graph = client.post(
            "/api/v1/workflows",
            json={"name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"},
        ).json()
        response = client.post(
            f"/api/v1/workflows/{graph['workflowId']}/instances/A/chat",
            json={"content": secret_content},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validationError"
        assert secret_content not in response.text
    store.close()
