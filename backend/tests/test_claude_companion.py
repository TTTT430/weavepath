from __future__ import annotations

import json
import importlib.util
import threading
from pathlib import Path

import httpx
import pytest

_SERVER_PATH = Path(__file__).resolve().parents[2] / "integrations" / "claude_code_companion" / "server.py"
_SPEC = importlib.util.spec_from_file_location("weavepath_claude_companion", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


def binding(session_id: str = "session-12345678") -> dict[str, str]:
    return {
        "workflowId": "wf", "instanceId": "root", "threadId": session_id,
        "provider": "claude-code",
    }


def test_claude_companion_lists_and_pages_exact_session_transcripts(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript = tmp_path / "projects" / "project-a" / "session-12345678.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(json.dumps({"type": "message", "index": index}) for index in range(3)) + "\n",
        encoding="utf-8",
    )
    listed = server.execute("listConversations", {})
    assert listed["items"][0]["threadId"] == "session-12345678"
    first = server.execute("inspect", {"binding": binding(), "limit": 2})
    assert [item["index"] for item in first["items"]] == [0, 1]
    assert first["nextCursor"] == "2"
    second = server.execute("inspect", {"binding": binding(), "limit": 2, "cursor": "2"})
    assert [item["index"] for item in second["items"]] == [2]
    assert second["nextCursor"] is None


def test_claude_companion_forks_only_the_session_head_and_returns_the_new_session(monkeypatch):
    monkeypatch.setattr(server, "CLAUDE_BIN", "claude")
    calls = []
    monkeypatch.setattr(server, "_run_claude", lambda args: calls.append(args) or {"session_id": "child-12345678"})
    result = server.execute("fork", {
        "source": binding(), "checkpoint": {"kind": "instanceHead"},
        "prompt": "continue", "options": {"targetInstanceId": "child"},
    }, "op-1")
    assert result["binding"]["threadId"] == "child-12345678"
    assert "--fork-session" in calls[0]
    with pytest.raises(ValueError):
        server.execute("fork", {
            "source": binding(), "checkpoint": {"kind": "localUserTurn"},
            "prompt": "continue", "options": {"targetInstanceId": "other"},
        })


def test_claude_companion_loopback_handshake_requires_the_bearer_token(monkeypatch):
    monkeypatch.setattr(server, "CLAUDE_BIN", None)
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_port}"
        assert httpx.get(base + "/v1/handshake").status_code == 401
        response = httpx.get(
            base + "/v1/handshake",
            headers={"Authorization": f"Bearer {server.TOKEN}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["contractVersion"] == 1
        assert payload["hostKind"] == "claude-code"
        assert payload["capabilities"]["canFork"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)
