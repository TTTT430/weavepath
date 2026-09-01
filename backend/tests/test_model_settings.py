from __future__ import annotations

import json

import pytest
import httpx
from fastapi.testclient import TestClient

from api.app import create_app
from api.model_settings import ModelConfig, ModelSettingsError, RuntimeModelSettings, validate_base_url
from graph_core import GraphStore


def test_secret_is_write_only_and_never_persisted(tmp_path):
    path = tmp_path / "model-settings.json"
    runtime = RuntimeModelSettings(path, env={})
    status = runtime.configure(
        base_url="https://example.test/v1", model="model-a", api_key="super-secret",
        persistence="local",
    )
    assert status["hasApiKey"] is True
    assert "apiKey" not in status and "super-secret" not in json.dumps(status)
    persisted = path.read_text(encoding="utf-8")
    assert "super-secret" not in persisted and "apiKey" not in persisted
    reloaded = RuntimeModelSettings(path, env={})
    assert reloaded.status()["hasApiKey"] is False
    assert reloaded.status()["model"] == "model-a"


def test_omitted_or_empty_key_keeps_existing_and_clear_is_explicit(tmp_path):
    runtime = RuntimeModelSettings(tmp_path / "settings.json", env={})
    runtime.configure(base_url="https://example.test/v1", model="a", api_key="key")
    runtime.configure(base_url="https://example.test/v1", model="b", api_key=None)
    assert runtime.status()["hasApiKey"] is True
    runtime.configure(base_url="https://example.test/v1", model="c", api_key="")
    assert runtime.status()["hasApiKey"] is True
    runtime.configure(base_url="https://example.test/v1", model="d", clear_api_key=True)
    assert runtime.status()["hasApiKey"] is False


def test_environment_fallback_and_url_policy(tmp_path):
    runtime = RuntimeModelSettings(tmp_path / "missing.json", env={
        "COTHINKER_LLM_BASE_URL": "https://provider.test/v1",
        "COTHINKER_LLM_MODEL": "env-model",
        "COTHINKER_LLM_API_KEY": "env-secret",
    })
    assert runtime.status()["source"] == "environment"
    assert runtime.status()["hasApiKey"] is True
    assert validate_base_url("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434/v1"
    for bad in ["http://example.com/v1", "file:///tmp/model", "https://user:pass@example.com/v1", "https://example.com/v1?q=x"]:
        with pytest.raises(ValueError):
            validate_base_url(bad)


def test_weavepath_model_environment_has_priority_over_legacy(tmp_path):
    runtime = RuntimeModelSettings(tmp_path / "missing.json", env={
        "WEAVEPATH_LLM_BASE_URL": "https://weavepath.test/v1",
        "WEAVEPATH_LLM_MODEL": "new-model",
        "WEAVEPATH_LLM_API_KEY": "new-key",
        "WEAVEPATH_LLM_TIMEOUT": "23",
        "WEAVEPATH_LLM_SYSTEM_PROMPT": "new prompt",
        "COTHINKER_LLM_BASE_URL": "https://legacy.test/v1",
        "COTHINKER_LLM_MODEL": "legacy-model",
        "COTHINKER_LLM_API_KEY": "legacy-key",
        "COTHINKER_LLM_TIMEOUT": "45",
        "COTHINKER_LLM_SYSTEM_PROMPT": "legacy prompt",
    })
    status = runtime.status()
    assert status["baseUrl"] == "https://weavepath.test/v1"
    assert status["model"] == "new-model"
    assert status["timeoutSeconds"] == 23
    assert status["systemPrompt"] == "new prompt"
    assert runtime._api_key == "new-key"


def test_invalid_environment_settings_do_not_prevent_startup_or_local_fallback(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "version": 1,
        "provider": "openai-compatible",
        "baseUrl": "https://local.test/v1",
        "model": "local-model",
        "timeoutSeconds": 30,
        "systemPrompt": "",
    }), encoding="utf-8")
    runtime = RuntimeModelSettings(path, env={
        "COTHINKER_LLM_BASE_URL": "http://remote.test/v1",
        "COTHINKER_LLM_MODEL": "env-model",
        "COTHINKER_LLM_API_KEY": "env-secret",
    })
    status = runtime.status()
    assert status["configured"] is True
    assert status["source"] == "local"
    assert status["model"] == "local-model"
    assert status["hasApiKey"] is True


def test_api_non_secret_readback_and_draft_validation_does_not_persist(tmp_path, monkeypatch):
    store = GraphStore(":memory:")
    runtime = RuntimeModelSettings(tmp_path / "settings.json", env={})
    captured: dict = {}

    def fake_discover(draft: ModelConfig | None = None, api_key: str | None = None):
        captured["draft"] = draft
        captured["apiKey"] = api_key
        return ["draft-model", "other"]

    monkeypatch.setattr(runtime, "discover_models", fake_discover)
    with TestClient(create_app(store, model_settings=runtime)) as client:
        put = client.put("/api/v1/ai/settings", json={
            "baseUrl": "https://saved.test/v1", "model": "saved", "apiKey": "secret"
        })
        assert put.status_code == 200 and put.json()["hasApiKey"] is True
        assert "apiKey" not in put.json()
        probe = client.post("/api/v1/ai/settings/validate", json={
            "baseUrl": "https://draft.test/v1", "model": "draft-model", "apiKey": "draft-key"
        })
        assert probe.json()["selectedModelAvailable"] is True
        assert captured["draft"].base_url == "https://draft.test/v1"
        assert captured["apiKey"] == "draft-key"
        assert client.get("/api/v1/ai/settings").json()["model"] == "saved"
    store.close()


def test_draft_validation_can_discover_models_before_selection(tmp_path, monkeypatch):
    store = GraphStore(":memory:")
    runtime = RuntimeModelSettings(tmp_path / "settings.json", env={})
    monkeypatch.setattr(runtime, "discover_models", lambda draft=None, api_key=None: ["alpha", "beta"])
    with TestClient(create_app(store, model_settings=runtime)) as client:
        probe = client.post("/api/v1/ai/settings/validate", json={
            "baseUrl": "http://127.0.0.1:1234/v1", "model": ""
        })
        assert probe.status_code == 200
        assert probe.json() == {
            "ok": True,
            "modelCount": 2,
            "selectedModelAvailable": False,
            "models": ["alpha", "beta"],
        }
        save = client.put("/api/v1/ai/settings", json={
            "baseUrl": "http://127.0.0.1:1234/v1", "model": ""
        })
        assert save.status_code == 422
    store.close()


def test_discovery_error_has_stable_code_and_no_upstream_body(tmp_path, monkeypatch):
    store = GraphStore(":memory:")
    runtime = RuntimeModelSettings(tmp_path / "settings.json", env={})
    def fail(*_args, **_kwargs):
        raise ModelSettingsError("modelDiscoveryFailed", "Unable to connect to the model provider")
    monkeypatch.setattr(runtime, "discover_models", fail)
    with TestClient(create_app(store, model_settings=runtime)) as client:
        response = client.post("/api/v1/ai/settings/validate", json={
            "baseUrl": "https://provider.test/v1", "model": "m", "apiKey": "never-leak"
        })
        assert response.status_code == 503
        assert response.json() == {
            "code": "modelDiscoveryFailed", "error": "Unable to connect to the model provider"
        }
        assert "never-leak" not in response.text
    store.close()


def test_openai_compatible_model_discovery_parses_and_sorts_ids(tmp_path, monkeypatch):
    runtime = RuntimeModelSettings(tmp_path / "settings.json", env={})
    runtime.configure(base_url="https://provider.test/v1", model="b", api_key="key")

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, url, headers):
            assert url == "https://provider.test/v1/models"
            assert headers["Authorization"] == "Bearer key"
            return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]},
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr("api.model_settings.httpx.Client", FakeClient)
    assert runtime.discover_models() == ["a", "b"]
    assert runtime.validate_connection(base_url="https://provider.test/v1", model="b") == {
        "ok": True, "modelCount": 2, "selectedModelAvailable": True, "models": ["a", "b"]
    }


@pytest.mark.parametrize(
    ("mode", "code", "status_code", "message"),
    [
        ("timeout", "modelDiscoveryTimeout", 504, "timed out"),
        ("unauthorized", "modelDiscoveryUnauthorized", 401, "rejected the API key"),
        ("unsupported", "modelDiscoveryUnsupported", 502, "compatible /models"),
        ("invalid", "modelDiscoveryInvalidResponse", 502, "invalid model list"),
    ],
)
def test_model_discovery_reports_actionable_failure_classes(
    tmp_path, monkeypatch, mode, code, status_code, message
):
    runtime = RuntimeModelSettings(tmp_path / "settings.json", env={})

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, url, headers):
            del headers
            request = httpx.Request("GET", url)
            if mode == "timeout":
                raise httpx.ConnectTimeout("timeout", request=request)
            if mode == "invalid":
                return httpx.Response(200, json=[], request=request)
            status = 401 if mode == "unauthorized" else 404
            return httpx.Response(status, json={"error": "must not leak"}, request=request)

    monkeypatch.setattr("api.model_settings.httpx.Client", FakeClient)
    with pytest.raises(ModelSettingsError) as caught:
        runtime.discover_models(ModelConfig("https://provider.test/v1", ""))
    assert caught.value.code == code
    assert caught.value.status_code == status_code
    assert message in str(caught.value)
    assert "must not leak" not in str(caught.value)


def test_request_validation_never_echoes_secret(tmp_path):
    store = GraphStore(":memory:")
    runtime = RuntimeModelSettings(tmp_path / "settings.json", env={})
    with TestClient(create_app(store, model_settings=runtime)) as client:
        response = client.put("/api/v1/ai/settings", json={
            "baseUrl": "https://provider.test/v1", "model": "x" * 201,
            "apiKey": "must-not-echo",
        })
        assert response.status_code == 422
        assert response.json()["code"] == "validationError"
        assert "must-not-echo" not in response.text
    store.close()
