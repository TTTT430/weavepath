from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.llm import OpenAICompatibleLLM, build_llm_from_env
from graph_core import GraphStore


class FakeClient:
    behavior = "timeout"

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, **_kwargs):
        request = httpx.Request("POST", url)
        if self.behavior == "timeout":
            raise httpx.ReadTimeout("upstream secret timeout detail", request=request)
        if self.behavior == "unavailable":
            return httpx.Response(
                401,
                text="provider body contains api-key-should-never-leak",
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "   "}}]},
            request=request,
        )


def test_build_llm_prefers_weavepath_environment_and_keeps_legacy_fallback(monkeypatch):
    values = {
        "WEAVEPATH_LLM_BASE_URL": "https://weavepath.test/v1",
        "WEAVEPATH_LLM_MODEL": "new-model",
        "WEAVEPATH_LLM_API_KEY": "new-key",
        "WEAVEPATH_LLM_TIMEOUT": "17",
        "WEAVEPATH_LLM_SYSTEM_PROMPT": "new prompt",
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
    assert client.system_prompt == "new prompt"

    for key in [name for name in values if name.startswith("WEAVEPATH_")]:
        monkeypatch.delenv(key)
    legacy = build_llm_from_env()
    assert isinstance(legacy, OpenAICompatibleLLM)
    assert (legacy.base_url, legacy.model, legacy.api_key) == (
        "https://legacy.test/v1", "legacy-model", "legacy-key"
    )


@pytest.mark.parametrize(
    ("behavior", "status", "code", "message"),
    [
        ("timeout", 504, "aiTimeout", "AI provider request timed out"),
        ("unavailable", 503, "aiUnavailable", "AI provider is unavailable"),
        ("empty", 502, "aiEmptyResponse", "AI provider returned an empty response"),
    ],
)
def test_chat_has_stable_safe_llm_error_protocol(
    tmp_path, monkeypatch, behavior, status, code, message
):
    FakeClient.behavior = behavior
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
    secret_content = "s" * 20_001
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
