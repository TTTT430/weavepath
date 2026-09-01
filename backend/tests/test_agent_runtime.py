from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_runtime import (AgentRunRepository, AgentRuntimeService, ModelTurn,
                           OpenAICompatibleAgentAdapter, ScriptedMockAgentAdapter,
                           calculator_registry)
from api.app import create_app
from api.llm import LLMUnavailable, OpenAICompatibleLLM
from graph_core import GraphStore
from graph_core.migrations import V1, V2


def request(revision: int, key: str = "run-key") -> dict:
    return {"objective": "Calculate the result", "constraints": ["Use calculator"],
            "deliverables": ["A number"], "acceptanceChecks": ["Correct arithmetic"],
            "expectedContentRevision": revision, "idempotencyKey": key}


def workflow(client: TestClient) -> tuple[str, int]:
    graph = client.post("/api/v1/workflows", json={
        "name": "Agent", "rootTitle": "A", "rootInstanceId": "A"
    }).json()
    return graph["workflowId"], graph["nodes"][0]["contentRevision"]


def test_happy_tool_run_persists_steps_events_and_final_message():
    store = GraphStore(":memory:")
    model = ScriptedMockAgentAdapter([
        ModelTurn(tool_name="safe_calculator", tool_arguments={"expression": "128 * 47"},
                  tool_call_id="provider-call-1"),
        ModelTurn(final_answer="6016"),
    ])
    app = create_app(store, agent_model=model)
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 201
        run = response.json()
        assert run["status"] == "completed" and run["finalMessageId"] is not None
        detail = client.get(f"/api/v1/runs/{run['runId']}").json()
        assert [step["kind"] for step in detail["steps"]] == ["model", "tool", "model"]
        assert detail["toolCalls"][0]["toolName"] == "safe_calculator"
        assert detail["toolCalls"][0]["toolVersion"] == "1.0.0"
        assert detail["toolResults"][0]["output"]["result"] == 6016
        assert detail["finalAnswer"] == "6016"
        events = client.get(f"/api/v1/runs/{run['runId']}/events").json()["events"]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert {"model.started", "tool.requested", "tool.started", "tool.completed"} <= {
            event["type"] for event in events
        }
        assert events[-1]["type"] == "run.completed"
        messages = store.list_messages(wf, "A", scope="local")["messages"]
        assert messages[-1]["content"] == "6016"
        assert client.get(f"/api/v1/workflows/{wf}/instances/A/runs").json()["runs"][0]["runId"] == run["runId"]
    store.close()


def test_context_snapshot_is_route_specific_and_excludes_sibling():
    store = GraphStore(":memory:")
    model = ScriptedMockAgentAdapter([ModelTurn(final_answer="done")])
    app = create_app(store, agent_model=model)
    with TestClient(app) as client:
        wf, _ = workflow(client)
        store.append_message(wf, "A", role="user", content="A fact")
        store.fork(wf, "A", title="B", instance_id="B", initial_message="B fact")
        store.fork(wf, "B", title="C", instance_id="C", initial_message="C fact")
        store.fork(wf, "A", title="E", instance_id="E", initial_message="E sibling secret")
        revision = store.list_messages(wf, "C", scope="local")["contentRevision"]
        result = client.post(f"/api/v1/workflows/{wf}/instances/C/runs", json=request(revision)).json()
        raw = store._conn.execute("SELECT context_snapshot_json FROM agent_runs WHERE id=?",
                                  (result["runId"],)).fetchone()[0]
        assert "A fact" in raw and "B fact" in raw and "C fact" in raw
        assert "E sibling secret" not in raw
        detail = client.get(f"/api/v1/runs/{result['runId']}").json()
        assert [node["instanceId"] for node in detail["memoryRoute"]] == ["A", "B", "C"]
        assert [(tool["name"], tool["version"]) for tool in detail["availableTools"]] == [
            ("safe_calculator", "1.0.0")
        ]
        assert "messages" not in detail
    store.close()


def test_frozen_context_and_hash_remain_stable_after_later_route_write():
    store = GraphStore(":memory:")
    app = create_app(
        store,
        agent_model=ScriptedMockAgentAdapter([ModelTurn(final_answer="frozen result")]),
    )
    with TestClient(app) as client:
        wf, _ = workflow(client)
        initial = store.append_message(wf, "A", role="user", content="frozen input")
        run = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs",
            json=request(initial["contentRevision"]),
        ).json()
        before = store._conn.execute(
            "SELECT context_snapshot_json,context_sha256 FROM agent_runs WHERE id=?",
            (run["runId"],),
        ).fetchone()
        frozen = json.loads(before["context_snapshot_json"])
        assert [message["content"] for message in frozen["messages"]] == ["frozen input"]
        assert frozen["objective"] == "Calculate the result"
        assert [(tool["name"], tool["version"]) for tool in frozen["availableTools"]] == [
            ("safe_calculator", "1.0.0")
        ]

        store.append_message(wf, "A", role="user", content="later route write")
        after = store._conn.execute(
            "SELECT context_snapshot_json,context_sha256 FROM agent_runs WHERE id=?",
            (run["runId"],),
        ).fetchone()
        assert after["context_snapshot_json"] == before["context_snapshot_json"]
        assert after["context_sha256"] == before["context_sha256"]
        assert client.get(f"/api/v1/runs/{run['runId']}").json()["contextSha256"] == before[
            "context_sha256"
        ]
    store.close()


def test_completed_run_keeps_immutable_result_when_chat_message_is_regenerated():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(final_answer="run result")]))
    with TestClient(app) as client:
        wf, _ = workflow(client)
        user = store.append_message(wf, "A", role="user", content="original question")
        run = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs",
            json=request(user["contentRevision"]),
        ).json()
        current_revision = store.list_messages(wf, "A", scope="local")["contentRevision"]
        store.commit_latest_local_user_edit(
            wf, "A", user["id"], content="edited question",
            expected_content_revision=current_revision, assistant_content="replacement answer",
        )
        assert "run result" not in {
            message["content"] for message in store.list_messages(wf, "A", scope="local")["messages"]
        }
        assert client.get(f"/api/v1/runs/{run['runId']}").json()["finalAnswer"] == "run result"
    store.close()


def test_unknown_and_invalid_tools_fail_before_execution():
    for turn, code in [
        (ModelTurn(tool_name="shell.exec", tool_arguments={"command": "whoami"}), "unknownTool"),
        (ModelTurn(tool_name="safe_calculator", tool_arguments={"wrong": "1+1"}), "toolArgumentsInvalid"),
    ]:
        store = GraphStore(":memory:")
        app = create_app(store, agent_model=ScriptedMockAgentAdapter([turn]))
        with TestClient(app) as client:
            wf, revision = workflow(client)
            response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
            assert response.status_code == 422 and response.json()["code"] == code
            run = app.state.agent_runs.list(wf, "A")[0]
            assert run["status"] == "failed" and run["errorCode"] == code
        store.close()


@pytest.mark.parametrize("turn", [
    ModelTurn(),
    ModelTurn(final_answer="answer", tool_call_id="orphan-call-id"),
    ModelTurn(tool_name="safe_calculator", tool_arguments=None),
])
def test_ambiguous_or_incomplete_model_turn_is_rejected(turn):
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([turn]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 502
        assert response.json()["code"] == "modelProtocolError"
        assert response.json()["runId"]
        run = app.state.agent_runs.list(wf, "A")[0]
        assert run["status"] == "failed"
    store.close()


def test_tool_failure_is_safe_and_durable():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([
        ModelTurn(tool_name="safe_calculator", tool_arguments={"expression": "1 / 0"})
    ]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 500
        assert response.json()["code"] == "toolExecutionFailed"
        assert response.json()["error"] == "Tool execution failed"
        assert response.json()["runId"]
        detail = app.state.agent_runs.get(app.state.agent_runs.list(wf, "A")[0]["runId"])
        assert detail["toolResults"][0]["errorCode"] == "toolExecutionFailed"
        assert "division" not in json.dumps(detail)
    store.close()


def test_revision_conflict_prevents_final_assistant_write():
    store = GraphStore(":memory:")
    state: dict[str, str] = {}
    model = ScriptedMockAgentAdapter([ModelTurn(final_answer="must not commit")],
        on_turn=lambda _: store.append_message(state["wf"], "A", role="user", content="concurrent"))
    app = create_app(store, agent_model=model)
    with TestClient(app) as client:
        wf, revision = workflow(client)
        state["wf"] = wf
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 409 and response.json()["code"] == "runRevisionConflict"
        assert response.json()["runId"]
        assert [m["content"] for m in store.list_messages(wf, "A", scope="local")["messages"]] == ["concurrent"]
    store.close()


def test_idempotency_returns_original_run_and_rejects_changed_request():
    store = GraphStore(":memory:")
    model = ScriptedMockAgentAdapter([ModelTurn(final_answer="done")])
    app = create_app(store, agent_model=model)
    with TestClient(app) as client:
        wf, revision = workflow(client)
        first = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        second = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert second.status_code == 201 and second.json()["runId"] == first.json()["runId"]
        assert model.calls == 1
        changed = request(revision)
        changed["objective"] = "different"
        conflict = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=changed)
        assert conflict.status_code == 409
    store.close()


def test_concurrent_idempotent_replay_waits_for_the_original_terminal_result():
    entered, release = threading.Event(), threading.Event()
    replay_started, replay_returned = threading.Event(), threading.Event()

    class BlockingModel:
        calls = 0

        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "test", "model": "blocking"}

        def next(self, messages, tools):
            del messages, tools
            self.calls += 1
            entered.set()
            assert release.wait(2), "test did not release the model"
            return ModelTurn(final_answer="one durable result")

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    model = BlockingModel()
    service = AgentRuntimeService(store, repository, model, calculator_registry())
    body = request(graph["nodes"][0]["contentRevision"], "same-concurrent-key")

    def replay_request():
        replay_started.set()
        result = service.execute(graph["workflowId"], "A", body)
        replay_returned.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.execute, graph["workflowId"], "A", body)
        assert entered.wait(1), "original run did not reach the model"
        replay = pool.submit(replay_request)
        assert replay_started.wait(1), "replay request did not start"
        assert not replay_returned.wait(0.05), "replay returned before the original reached terminal state"
        release.set()
        first_result, replay_result = first.result(timeout=2), replay.result(timeout=2)

    assert first_result["status"] == replay_result["status"] == "completed"
    assert first_result["runId"] == replay_result["runId"]
    assert first_result["finalAnswer"] == replay_result["finalAnswer"] == "one durable result"
    assert model.calls == 1
    store.close()


def test_startup_recovery_marks_running_once_and_event_sequence_remains_stable():
    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    wf = graph["workflowId"]
    repo = AgentRunRepository(store._conn, store._lock)
    run, _ = repo.create(workflow_id=wf, instance_id="A", request=request(0),
                         context={"messages": []}, model_snapshot={"provider": "mock", "model": "x"})
    queued, _ = repo.create(workflow_id=wf, instance_id="A", request=request(0, "queued-key"),
                            context={"messages": []}, model_snapshot={"provider": "mock", "model": "x"})
    repo.start(run["runId"])
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([]))
    with TestClient(app):
        assert repo.get(run["runId"])["status"] == "interrupted"
        assert repo.get(queued["runId"])["status"] == "interrupted"
    with TestClient(app):
        for run_id in (run["runId"], queued["runId"]):
            events = repo.events(run_id, 0, 100)["events"]
            assert [event["type"] for event in events].count("run.interrupted") == 1
            assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    store.close()


def test_schema_v1_database_upgrades_to_latest_without_losing_graph_data(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(V1)
    conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_migrations VALUES(1,'old')")
    conn.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?)",
                 ("legacy", "Legacy workflow", "A", "A", 0, 1, "old", "old"))
    conn.execute("INSERT INTO topics VALUES(?,?,?,?)", ("topic", "legacy", "Root topic", "old"))
    conn.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?)", ("cp", "legacy", None, 0, "[]", "old"))
    conn.execute("INSERT INTO conversation_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("A", "legacy", "topic", None, "cp", "Legacy root", "active", "local",
                  None, 1, "old", "old"))
    conn.execute("INSERT INTO topics VALUES(?,?,?,?)", ("child-topic", "legacy", "Child", "old"))
    conn.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?)", ("child-cp", "legacy", "A", 1, "[]", "old"))
    conn.execute("INSERT INTO conversation_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("B", "legacy", "child-topic", "A", "child-cp", "Legacy child", "active",
                  "local", None, 0, "old", "old"))
    conn.execute("INSERT INTO local_messages(workflow_id,instance_id,role,content,created_at) "
                 "VALUES(?,?,?,?,?)", ("legacy", "A", "user", "preserve me", "old"))
    conn.commit()
    conn.close()
    store = GraphStore(path)
    versions = [row[0] for row in store._conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [1, 2, 3, 4]
    assert store._conn.execute("SELECT name FROM sqlite_master WHERE name='agent_runs'").fetchone()
    assert store.get_graph("legacy")["nodes"][0]["title"] == "Legacy root"
    child = next(node for node in store.get_graph("legacy")["nodes"] if node["id"] == "B")
    assert child["checkpointAnchor"] == {
        "kind": "instanceHead",
        "cursorValue": "1",
        "sourceInstanceId": "A",
        "sourceContentRevision": 1,
        "anchorMessageId": None,
    }
    assert store.list_messages("legacy", "A", scope="local")["messages"][0]["content"] == "preserve me"
    store.create_workflow(name="after migration", root_title="A")
    store.close()


def test_schema_v2_upgrade_backfills_immutable_completed_run_result(tmp_path):
    path = tmp_path / "runtime-v2.db"
    conn = sqlite3.connect(path)
    conn.executescript(V1)
    conn.executescript(V2)
    conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
    conn.executemany("INSERT INTO schema_migrations VALUES(?,?)", [(1, "old"), (2, "old")])
    conn.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?)",
                 ("legacy", "Legacy workflow", "A", "A", 0, 1, "old", "old"))
    conn.execute("INSERT INTO topics VALUES(?,?,?,?)", ("topic", "legacy", "Root", "old"))
    conn.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?)", ("cp", "legacy", None, 0, "[]", "old"))
    conn.execute("INSERT INTO conversation_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("A", "legacy", "topic", None, "cp", "Root", "active", "local", None, 1, "old", "old"))
    message_id = conn.execute(
        "INSERT INTO local_messages(workflow_id,instance_id,role,content,created_at) VALUES(?,?,?,?,?)",
        ("legacy", "A", "assistant", "durable answer", "old"),
    ).lastrowid
    conn.execute(
        "INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("run_legacy", "legacy", "A", "completed", 0, "{}", "context-hash", "{}", "{}",
         "request-hash", "legacy-key", "objective", "[]", "[]", "[]", message_id, None,
         "old", "old"),
    )
    conn.commit()
    conn.close()

    store = GraphStore(path)
    repo = AgentRunRepository(store._conn, store._lock)
    assert repo.get("run_legacy")["finalAnswer"] == "durable answer"
    assert [row[0] for row in store._conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    )] == [1, 2, 3, 4]
    store.close()
    reopened = GraphStore(path)
    assert AgentRunRepository(reopened._conn, reopened._lock).get("run_legacy")["finalAnswer"] == "durable answer"
    reopened.close()


def test_model_snapshot_never_contains_api_key():
    adapter = OpenAICompatibleAgentAdapter(lambda: OpenAICompatibleLLM(
        base_url="https://provider.test/v1", model="m", api_key="never-store-this-secret"
    ))
    assert "never-store-this-secret" not in json.dumps(adapter.snapshot())


def test_secret_fields_from_custom_model_snapshot_are_not_persisted_or_returned():
    secret = "secret-that-must-never-leave-memory"

    class SecretSnapshotModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "custom", "model": "safe-model", "apiKey": secret,
                    "accessToken": secret, "nested": {"token": secret}}

        def next(self, messages, tools):
            del messages, tools
            return ModelTurn(final_answer="safe answer")

    store = GraphStore(":memory:")
    app = create_app(store, agent_model=SecretSnapshotModel())
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 201
        run_id = response.json()["runId"]
        detail = client.get(f"/api/v1/runs/{run_id}").json()
        assert detail["modelSnapshot"] == {"provider": "custom", "model": "safe-model"}
        stored = store._conn.execute(
            "SELECT model_snapshot_json FROM agent_runs WHERE id=?", (run_id,)
        ).fetchone()[0]
        assert secret not in stored
        assert secret not in json.dumps(detail)
    store.close()


def test_snapshot_url_with_embedded_credentials_is_rejected_before_run_creation():
    class UnsafeSnapshotModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "custom", "model": "unsafe",
                    "baseUrl": "https://user:secret@provider.test/v1"}

        def next(self, messages, tools):
            raise AssertionError("model must not be called")

    store = GraphStore(":memory:")
    app = create_app(store, agent_model=UnsafeSnapshotModel())
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 502
        assert response.json() == {"code": "modelProtocolError", "error": "Model snapshot is invalid"}
        assert app.state.agent_runs.list(wf, "A") == []
    store.close()


def test_safe_calculator_rejects_unbounded_exponent_before_evaluation():
    registry = calculator_registry()
    tool = registry.resolve("safe_calculator")
    assert tool is not None
    with pytest.raises(ValueError, match="exponent"):
        tool.execute({"expression": "9 ** 9 ** 9"})
    for expression in ("1e309", "-1e309", "nan", "inf", "10 ** 101", "(-1) ** 0.5", "9" * 200):
        with pytest.raises(ValueError):
            tool.execute({"expression": expression})


def test_safe_calculator_rejects_oversized_expression_at_registry_boundary():
    registry = calculator_registry()
    tool = registry.resolve("safe_calculator")
    assert tool is not None
    with pytest.raises(ValueError, match="1-200 characters"):
        registry.validate(tool, {"expression": "1" * 201})


def test_openai_adapter_disables_parallel_calls_and_accepts_one_complete_tool_call(monkeypatch):
    captured: dict = {}
    original_client = httpx.Client

    def handler(request_: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request_.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "call-1", "type": "function",
                "function": {"name": "safe_calculator", "arguments": "{\"expression\":\"2+2\"}"}}]
        }}]})

    monkeypatch.setattr(
        "agent_runtime.adapters.httpx.Client",
        lambda timeout: original_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )
    adapter = OpenAICompatibleAgentAdapter(lambda: OpenAICompatibleLLM(
        base_url="https://provider.test/v1", model="test-model"
    ))
    turn = adapter.next([], calculator_registry().specs())
    assert turn.tool_name == "safe_calculator"
    assert turn.tool_arguments == {"expression": "2+2"}
    assert captured["payload"]["parallel_tool_calls"] is False


@pytest.mark.parametrize("choice", [
    {"finish_reason": "length", "message": {"content": "partial answer"}},
    {"finish_reason": "content_filter", "message": {"content": "filtered"}},
    {"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": "one", "type": "function", "function": {"name": "safe_calculator", "arguments": "{}"}},
        {"id": "two", "type": "function", "function": {"name": "safe_calculator", "arguments": "{}"}},
    ]}},
    {"finish_reason": "stop", "message": {"content": None, "tool_calls": [
        {"id": "one", "type": "function", "function": {"name": "safe_calculator", "arguments": "{}"}},
    ]}},
    {"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": "one", "type": "function", "function": {
            "name": "safe_calculator", "arguments": "{\"expression\":NaN}"
        }},
    ]}},
])
def test_openai_adapter_rejects_truncated_filtered_or_ambiguous_turns(monkeypatch, choice):
    original_client = httpx.Client
    monkeypatch.setattr(
        "agent_runtime.adapters.httpx.Client",
        lambda timeout: original_client(
            timeout=timeout,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [choice]})),
        ),
    )
    adapter = OpenAICompatibleAgentAdapter(lambda: OpenAICompatibleLLM(
        base_url="https://provider.test/v1", model="test-model"
    ))
    with pytest.raises(ValueError, match="invalid model protocol response"):
        adapter.next([], calculator_registry().specs())


def test_provider_error_code_is_preserved_in_run_and_events():
    class TimeoutModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "test", "model": "timeout"}

        def next(self, messages, tools):
            del messages, tools
            raise LLMUnavailable("timed out", code="aiTimeout", status_code=504)

    store = GraphStore(":memory:")
    app = create_app(store, agent_model=TimeoutModel())
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 504 and response.json()["code"] == "aiTimeout"
        run = app.state.agent_runs.list(wf, "A")[0]
        assert run["status"] == "failed" and run["errorCode"] == "aiTimeout"
        events = app.state.agent_runs.events(run["runId"], 0, 100)["events"]
        assert any(event["type"] == "model.failed" and
                   event["payload"]["errorCode"] == "aiTimeout" for event in events)
        replay = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert replay.status_code == 201
        assert replay.json()["runId"] == run["runId"]
        assert replay.json()["status"] == "failed"
    store.close()


def test_redacted_event_journal_remains_ordered_after_database_reopen(tmp_path):
    path = tmp_path / "redacted-events.db"
    secret = "provider-secret-that-must-not-persist"

    class TimeoutModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "test", "model": "timeout"}

        def next(self, messages, tools):
            del messages, tools
            raise LLMUnavailable(secret, code="aiTimeout", status_code=504)

    store = GraphStore(path)
    app = create_app(store, agent_model=TimeoutModel())
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        )
        assert response.status_code == 504
        assert secret not in response.text
        run_id = response.json()["runId"]
    store.close()

    reopened = GraphStore(path)
    reopened_app = create_app(reopened, agent_model=ScriptedMockAgentAdapter([]))
    with TestClient(reopened_app) as client:
        detail = client.get(f"/api/v1/runs/{run_id}")
        event_response = client.get(f"/api/v1/runs/{run_id}/events")
        assert detail.status_code == event_response.status_code == 200
        events = event_response.json()["events"]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert events[-2]["type"] == "model.failed"
        assert events[-1]["type"] == "run.failed"
        persisted = json.dumps(
            {"detail": detail.json(), "events": events}, ensure_ascii=False
        )
        assert secret not in persisted
        assert detail.json()["errorCode"] == "aiTimeout"
        assert events[-1]["payload"] == {"errorCode": "aiTimeout"}
    reopened.close()


def test_bind_failure_is_redacted_normalized_and_creates_no_run():
    secret = "token=must-not-leak"

    class BadBindModel:
        def bind(self):
            raise LLMUnavailable(secret, code="providerSecret", status_code=418)

        def snapshot(self):
            raise AssertionError("snapshot must not be called")

        def next(self, messages, tools):
            raise AssertionError("model must not be called")

    store = GraphStore(":memory:")
    app = create_app(store, agent_model=BadBindModel())
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 503
        assert response.json() == {
            "code": "aiUnavailable", "error": "Agent provider is unavailable"
        }
        assert secret not in response.text
        assert app.state.agent_runs.list(wf, "A") == []
    store.close()


def test_execution_brief_rejects_blank_or_oversized_items_before_run_creation():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(final_answer="unused")]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        blank = request(revision)
        blank["constraints"] = ["   "]
        assert client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=blank).status_code == 422
        oversized = request(revision, "other-key")
        oversized["deliverables"] = ["x" * 2_001]
        assert client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=oversized).status_code == 422
        assert app.state.agent_runs.list(wf, "A") == []
    store.close()


def test_stale_revision_creates_no_run_or_event():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(final_answer="unused")]))
    with TestClient(app) as client:
        wf, stale_revision = workflow(client)
        store.append_message(wf, "A", role="user", content="route changed")
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(stale_revision)
        )
        assert response.status_code == 409
        assert response.json()["code"] == "runRevisionConflict"
        assert store._conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
        assert store._conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 0
    store.close()


def test_pruned_target_is_rejected_with_stable_code_before_run_creation():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(final_answer="unused")]))
    with TestClient(app) as client:
        wf, _ = workflow(client)
        child = store.fork(wf, "A", title="B", instance_id="B")["node"]
        graph_revision = store.get_graph(wf)["graphRevision"]
        store.prune_commit(
            wf, "B", expected_revision=graph_revision, idempotency_key="prune-b"
        )
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/B/runs",
            json=request(child["contentRevision"]),
        )
        assert response.status_code == 409
        assert response.json() == {
            "code": "runTargetInactive", "error": "Conversation instance is not active"
        }
        assert app.state.agent_runs.list(wf, "B") == []
    store.close()


def test_late_model_completion_cannot_revive_an_interrupted_run():
    store = GraphStore(":memory:")
    state: dict = {}
    model = ScriptedMockAgentAdapter(
        [ModelTurn(final_answer="late answer")],
        on_turn=lambda _: state["repository"].recover_interrupted(),
    )
    app = create_app(store, agent_model=model)
    state["repository"] = app.state.agent_runs
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 409
        assert response.json()["code"] == "runInterrupted"
        run_id = response.json()["runId"]
        run = app.state.agent_runs.get(run_id)
        assert run["status"] == "interrupted"
        assert run["finalAnswer"] is None
        assert store.list_messages(wf, "A", scope="local")["messages"] == []
        event_types = [event["type"] for event in app.state.agent_runs.events(run_id, 0, 100)["events"]]
        assert "run.interrupted" in event_types
        assert "run.completed" not in event_types
        assert "run.failed" not in event_types
    store.close()


def test_unexpected_model_exception_returns_redacted_json_and_durable_failed_run():
    class ExplodingModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "custom", "model": "exploding"}

        def next(self, messages, tools):
            del messages, tools
            raise RuntimeError("upstream leaked secret value")

    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ExplodingModel())
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision))
        assert response.status_code == 503
        assert response.json()["code"] == "aiUnavailable"
        assert response.json()["error"] == "Agent run failed"
        assert "secret value" not in response.text
        run_id = response.json()["runId"]
        detail = client.get(f"/api/v1/runs/{run_id}").json()
        assert detail["status"] == "failed"
        assert detail["errorCode"] == "aiUnavailable"
        assert "secret value" not in json.dumps(detail)
    store.close()


def test_completed_run_result_survives_database_reopen(tmp_path):
    path = tmp_path / "runtime.db"
    store = GraphStore(path)
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(final_answer="persisted")]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        run_id = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        ).json()["runId"]
    store.close()

    reopened = GraphStore(path)
    detail = AgentRunRepository(reopened._conn, reopened._lock).get(run_id)
    assert detail["status"] == "completed"
    assert detail["finalAnswer"] == "persisted"
    reopened.close()
