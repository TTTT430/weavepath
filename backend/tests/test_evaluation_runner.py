import importlib.util
import json
from pathlib import Path

import pytest
import httpx

from agent_runtime.adapters import ModelTurn, OpenAICompatibleAgentAdapter


def runner():
    path = Path(__file__).resolve().parents[2] / "scripts/evaluate_agent.py"
    spec = importlib.util.spec_from_file_location("evaluation_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_requires_explicit_credentials(monkeypatch, tmp_path):
    for key in ("EVAL_BASE_URL", "EVAL_MODEL", "EVAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="Live evaluation requires"):
        runner().live(tmp_path / "report.json")


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422, 429, 500])
def test_diagnostics_preserve_status_without_provider_secrets(status):
    llm = runner().DiagnosticLLM(base_url="https://example.test/v1", model="test")
    response = httpx.Response(status, text="secret-provider-body", request=httpx.Request("POST", "https://example.test"))
    llm._transport_error(httpx.HTTPStatusError("secret-error", request=response.request, response=response))
    assert llm.diagnostic["httpStatus"] == status
    assert "secret" not in json.dumps(llm.diagnostic)


def test_provider_parameter_error_and_request_id_are_reported_safely():
    llm = runner().DiagnosticLLM(base_url="https://example.test/v1", model="test", api_key="sensitive-test-key")
    response = httpx.Response(400, json={"error": {"type": "invalid_request_error",
        "code": "unsupported_parameter", "param": "reasoning_effort",
        "message": "Invalid reasoning_effort; bearer sensitive-test-key is unsupported"}},
        headers={"x-request-id": "req_test-123"},
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"))
    llm._transport_error(httpx.HTTPStatusError("bad request", request=response.request, response=response))
    diagnostic = llm.diagnostic
    assert diagnostic["providerType"] == "invalid_request_error"
    assert diagnostic["providerCode"] == "unsupported_parameter"
    assert diagnostic["providerParam"] == "reasoning_effort"
    assert diagnostic["requestId"] == "req_test-123"
    assert "sensitive-test-key" not in json.dumps(diagnostic)


def test_provider_body_with_echoed_prompt_is_not_persisted():
    llm = runner().DiagnosticLLM(base_url="https://example.test/v1", model="test")
    response = httpx.Response(400, json={"error": {"message": "Request rejected: " +
        "user prompt with private fact " * 25 + " tools"}},
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"))
    llm._transport_error(httpx.HTTPStatusError("bad request", request=response.request, response=response))
    assert llm.diagnostic["providerMessage"] == "[provider message omitted]"
    assert "private fact" not in json.dumps(llm.diagnostic)


def test_provider_quota_code_overrides_generic_http_400_hint():
    llm = runner().DiagnosticLLM(base_url="https://example.test/v1", model="test")
    response = httpx.Response(400, json={"error": {"type": "invalid_request_error",
        "code": "insufficient_quota"}},
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"))
    llm._transport_error(httpx.HTTPStatusError("bad request", request=response.request, response=response))
    assert llm.diagnostic["providerCode"] == "insufficient_quota"
    assert "额度不足" in llm.diagnostic["hint"]
    assert "reasoning_effort" not in llm.diagnostic["hint"]


def test_live_runner_uses_runtime_and_keeps_human_review_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("EVAL_MODEL", "test-model")
    monkeypatch.setenv("EVAL_API_KEY", "secret-eval-token")
    monkeypatch.setenv("WEAVEPATH_HOST_BRIDGE_DISCOVERY", "absent")
    monkeypatch.setenv("WEAVEPATH_HOST_BRIDGE_URL", "")
    turns = iter([
        ModelTurn(tool_name="safe_calculator", tool_arguments={"expression": "47*128"}, tool_call_id="eval-tool"),
        ModelTurn(final_answer="6016"), ModelTurn(final_answer="Unknown"), ModelTurn(final_answer="BM25 matches keywords."),
    ])
    monkeypatch.setattr(OpenAICompatibleAgentAdapter, "next", lambda *_: next(turns))
    output = tmp_path / "report.json"
    assert runner().live(output) == 0
    text = output.read_text(encoding="utf-8")
    assert "secret-eval-token" not in text
    rows = json.loads(text)["results"]
    assert len(rows) == 3
    assert rows[0]["rulePassed"] is True
    assert rows[0]["metrics"]["toolCallCount"] == 1
    assert all(row["humanReview"] == "pending" for row in rows)
    assert rows[1]["rulePassed"] is None


def test_provider_failure_stops_remaining_cases(monkeypatch, tmp_path):
    for key, value in {"EVAL_BASE_URL": "https://example.test/v1", "EVAL_MODEL": "test",
                       "EVAL_API_KEY": "secret", "WEAVEPATH_HOST_BRIDGE_DISCOVERY": "absent",
                       "WEAVEPATH_HOST_BRIDGE_URL": ""}.items():
        monkeypatch.setenv(key, value)
    calls = []
    def fail(adapter, *_):
        calls.append(1)
        response = httpx.Response(401, request=httpx.Request("POST", "https://example.test"))
        raise adapter.client_factory()._transport_error(
            httpx.HTTPStatusError("unauthorized", request=response.request, response=response))
    monkeypatch.setattr(OpenAICompatibleAgentAdapter, "next", fail)
    output = tmp_path / "report.json"
    assert runner().live(output) == 1
    rows = json.loads(output.read_text(encoding="utf-8"))["results"]
    assert len(calls) == 1
    assert rows[0]["diagnostic"]["httpStatus"] == 401
    assert [r["status"] for r in rows] == ["failed", "skipped", "skipped"]
