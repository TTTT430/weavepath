from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_runtime import (AgentRunRepository, AgentRuntimeService, ModelTurn,
                           OpenAICompatibleAgentAdapter, ScriptedMockAgentAdapter,
                           calculator_registry, runtime_registry)
from agent_runtime.adapters import _usage
from agent_runtime.tools import Tool, ToolRegistry
from api.app import create_app
from api.llm import LLMUnavailable, OpenAICompatibleLLM
from engineering import EngineeringRepository
from graph_core import Conflict, GraphStore
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


def test_background_run_returns_immediately_and_survives_browser_request_lifetime():
    store = GraphStore(":memory:")
    started, release = threading.Event(), threading.Event()

    def block_model(_: int) -> None:
        started.set()
        assert release.wait(2)

    model = ScriptedMockAgentAdapter([ModelTurn(final_answer="background result")], block_model)
    app = create_app(store, agent_model=model, background_agent_runs=True)
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision),
        )
        assert response.status_code == 201
        run = response.json()
        assert run["status"] in {"queued", "running"}
        assert started.wait(1)
        assert client.get(f"/api/v1/runs/{run['runId']}").json()["status"] == "running"
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            completed = client.get(f"/api/v1/runs/{run['runId']}").json()
            if completed["status"] == "completed":
                break
            time.sleep(0.01)
        assert completed["status"] == "completed"
        assert completed["finalAnswer"] == "background result"
    store.close()


def test_process_startup_resumes_a_durable_queued_run_before_any_model_call():
    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    wf = graph["workflowId"]
    model = ScriptedMockAgentAdapter([ModelTurn(final_answer="resumed result")])
    staging = create_app(store, agent_model=model, background_agent_runs=False)
    queued = staging.state.agent_runtime.enqueue(
        wf, "A", request(0, "queued-before-restart"), lambda _run_id: None,
    )
    assert queued["status"] == "queued"

    restarted = create_app(store, agent_model=model, background_agent_runs=True)
    with TestClient(restarted) as client:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            completed = client.get(f"/api/v1/runs/{queued['runId']}").json()
            if completed["status"] == "completed":
                break
            time.sleep(0.01)
        assert completed["status"] == "completed"
        assert completed["finalAnswer"] == "resumed result"
        events = client.get(f"/api/v1/runs/{queued['runId']}/events").json()["events"]
        assert [event["type"] for event in events].count("run.started") == 1
    store.close()


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
        assert detail["metrics"]["modelStepCount"] == 2
        assert detail["metrics"]["toolCallCount"] == 1
        assert detail["metrics"]["toolDurationMs"] >= 0
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
            ("propose_patch", "1.0.0"), ("safe_calculator", "1.0.0")
        ]
        assert "messages" not in detail
    store.close()


def test_agent_runtime_materializes_bound_attachment_references_before_model_call():
    captured: list[list[dict[str, Any]]] = []

    class CapturingModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "test", "model": "attachment-capture"}

        def next(self, messages, tools):
            del tools
            captured.append(messages)
            return ModelTurn(final_answer="attachment received")

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    wf = graph["workflowId"]
    uploaded = store.create_attachment(
        wf, "A", name="requirements.txt", mime_type="text/plain",
        size_bytes=28, content_text="stable attachment requirement",
    )
    envelope = "[WeavePath attachments v2]\n" + json.dumps({
        "version": 2,
        "prompt": "Review the attached requirements.",
        "files": [{
            "attachmentId": uploaded["attachmentId"],
            "name": uploaded["name"],
            "mimeType": uploaded["mimeType"],
            "size": uploaded["size"],
        }],
    })
    message = store.append_message(wf, "A", role="user", content=envelope)
    app = create_app(store, agent_model=CapturingModel())

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs",
            json=request(message["contentRevision"], "attachment-agent-run"),
        )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert len(captured) == 1
    serialized = json.dumps(captured[0], ensure_ascii=False)
    assert "Review the attached requirements." in serialized
    assert "stable attachment requirement" in serialized
    assert "[WeavePath attachments v2]" not in serialized
    assert uploaded["attachmentId"] not in serialized
    store.close()


def test_agent_runtime_automatically_retrieves_only_the_live_route_and_exposes_plan():
    captured: list[list[dict[str, Any]]] = []

    class CapturingModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "test", "model": "automatic-retrieval"}

        def next(self, messages, tools):
            del tools
            captured.append(messages)
            return ModelTurn(final_answer="retrieval complete")

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    wf = graph["workflowId"]
    inherited = store.create_attachment(
        wf, "A", name="parent-evidence.txt", mime_type="text/plain",
        size_bytes=64, content_text="Agent retrieval marker PARENT-4411.",
    )
    store.fork(wf, "A", title="C", instance_id="C")
    store.fork(wf, "A", title="E", instance_id="E")
    sibling = store.create_attachment(
        wf, "E", name="sibling-evidence.txt", mime_type="text/plain",
        size_bytes=64, content_text="Agent retrieval marker SIBLING-5522.",
    )
    revision = store.list_messages(wf, "C", scope="local")["contentRevision"]
    body = request(revision, "automatic-route-retrieval")
    body["objective"] = "Inspect the agent retrieval marker."
    app = create_app(store, agent_model=CapturingModel())

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/C/runs", json=body
        )

    assert response.status_code == 201
    assert len(captured) == 1
    serialized = json.dumps(captured[0], ensure_ascii=False)
    assert "PARENT-4411" in serialized
    assert "SIBLING-5522" not in serialized
    run = response.json()
    assert run["retrievalPlan"]["sources"][0]["attachmentId"] == inherited["attachmentId"]
    assert run["retrievalPlan"]["routeInstanceIds"] == ["A", "C"]
    assert sibling["attachmentId"] not in str(run["retrievalPlan"])
    raw = store._conn.execute(
        "SELECT context_snapshot_json FROM agent_runs WHERE id=?", (run["runId"],)
    ).fetchone()[0]
    frozen = json.loads(raw)
    assert frozen["retrievalPlan"]["contextSha256"]
    assert "PARENT-4411" in frozen["retrievedEvidence"]
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
            ("propose_patch", "1.0.0"), ("safe_calculator", "1.0.0")
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


def test_parent_route_revision_change_rejects_stale_child_run_writeback():
    store = GraphStore(":memory:")
    state: dict[str, str] = {}
    model = ScriptedMockAgentAdapter(
        [ModelTurn(final_answer="must not commit to C")],
        on_turn=lambda _: store.append_message(
            state["wf"], "B", role="user", content="new parent context"
        ),
    )
    app = create_app(store, agent_model=model)
    with TestClient(app) as client:
        wf, _ = workflow(client)
        state["wf"] = wf
        store.append_message(wf, "A", role="user", content="A context")
        store.fork(wf, "A", title="B", instance_id="B", initial_message="B context")
        store.fork(wf, "B", title="C", instance_id="C", initial_message="C request")
        revision = store.list_messages(wf, "C", scope="local")["contentRevision"]

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/C/runs",
            json=request(revision, "parent-revision-conflict"),
        )

        assert response.status_code == 409
        assert response.json()["code"] == "runRevisionConflict"
        detail = client.get(f"/api/v1/runs/{response.json()['runId']}").json()
        assert detail["status"] == "failed"
        assert [item["instanceId"] for item in detail["routeRevisionVector"]] == [
            "A", "B", "C"
        ]
        assert "must not commit to C" not in {
            message["content"]
            for message in store.list_messages(wf, "C", scope="local")["messages"]
        }
        assert "new parent context" in {
            message["content"]
            for message in store.list_messages(wf, "B", scope="local")["messages"]
        }
    store.close()


def test_sibling_revision_change_does_not_conflict_with_child_run_writeback():
    store = GraphStore(":memory:")
    state: dict[str, str] = {}
    model = ScriptedMockAgentAdapter(
        [ModelTurn(final_answer="C may commit")],
        on_turn=lambda _: store.append_message(
            state["wf"], "E", role="user", content="sibling-only update"
        ),
    )
    app = create_app(store, agent_model=model)
    with TestClient(app) as client:
        wf, _ = workflow(client)
        state["wf"] = wf
        store.append_message(wf, "A", role="user", content="A context")
        store.fork(wf, "A", title="B", instance_id="B", initial_message="B context")
        store.fork(wf, "B", title="C", instance_id="C", initial_message="C request")
        store.fork(wf, "B", title="E", instance_id="E", initial_message="E request")
        revision = store.list_messages(wf, "C", scope="local")["contentRevision"]

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/C/runs",
            json=request(revision, "sibling-revision-does-not-conflict"),
        )

        assert response.status_code == 201
        detail = response.json()
        assert detail["status"] == "completed"
        assert detail["finalAnswer"] == "C may commit"
        assert [item["instanceId"] for item in detail["routeRevisionVector"]] == [
            "A", "B", "C"
        ]
        assert "C may commit" in {
            message["content"]
            for message in store.list_messages(wf, "C", scope="local")["messages"]
        }
        assert "sibling-only update" not in {
            message["content"]
            for message in store.list_messages(wf, "C", scope="effective")["messages"]
        }
    store.close()


def test_accepted_knowledge_change_on_parent_rejects_stale_child_run_writeback():
    store = GraphStore(":memory:")
    state: dict[str, Any] = {}

    def merge_into_parent(_: int) -> None:
        state["engineering"].merge_knowledge(
            state["wf"], target_instance_id="B", source_instance_ids=["E"],
            items=[{
                "sourceInstanceId": "E", "kind": "constraint", "title": "New rule",
                "content": "This arrived after the model input was assembled.",
            }], artifact_ids=[],
        )

    model = ScriptedMockAgentAdapter(
        [ModelTurn(final_answer="stale knowledge answer")], on_turn=merge_into_parent,
    )
    app = create_app(store, agent_model=model)
    state["engineering"] = app.state.engineering
    with TestClient(app) as client:
        wf, _ = workflow(client)
        state["wf"] = wf
        store.fork(wf, "A", title="B", instance_id="B", initial_message="B context")
        store.fork(wf, "B", title="C", instance_id="C", initial_message="C request")
        store.fork(wf, "B", title="E", instance_id="E", initial_message="E evidence")
        revision = store.list_messages(wf, "C", scope="local")["contentRevision"]

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/C/runs",
            json=request(revision, "knowledge-parent-conflict"),
        )

        assert response.status_code == 409
        assert response.json()["code"] == "runRevisionConflict"
        assert "stale knowledge answer" not in {
            message["content"]
            for message in store.list_messages(wf, "C", scope="local")["messages"]
        }
    store.close()


def test_accepted_knowledge_change_on_sibling_does_not_conflict_with_child_run():
    store = GraphStore(":memory:")
    state: dict[str, Any] = {}

    def merge_into_sibling(_: int) -> None:
        state["engineering"].merge_knowledge(
            state["wf"], target_instance_id="E", source_instance_ids=["C"],
            items=[{
                "sourceInstanceId": "C", "kind": "fact", "title": "Sibling fact",
                "content": "This belongs only to E and its descendants.",
            }], artifact_ids=[],
        )

    model = ScriptedMockAgentAdapter(
        [ModelTurn(final_answer="C remains valid")], on_turn=merge_into_sibling,
    )
    app = create_app(store, agent_model=model)
    state["engineering"] = app.state.engineering
    with TestClient(app) as client:
        wf, _ = workflow(client)
        state["wf"] = wf
        store.fork(wf, "A", title="B", instance_id="B", initial_message="B context")
        store.fork(wf, "B", title="C", instance_id="C", initial_message="C request")
        store.fork(wf, "B", title="E", instance_id="E", initial_message="E request")
        revision = store.list_messages(wf, "C", scope="local")["contentRevision"]

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/C/runs",
            json=request(revision, "knowledge-sibling-no-conflict"),
        )

        assert response.status_code == 201
        assert response.json()["status"] == "completed"
        assert response.json()["finalAnswer"] == "C remains valid"
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


def test_runtime_lease_has_one_owner_and_records_heartbeat_phase():
    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    run, _ = repository.create(
        workflow_id=graph["workflowId"], instance_id="A", request=request(0),
        context={"messages": []}, model_snapshot={"provider": "mock", "model": "x"},
    )

    owner = repository.start(run["runId"], owner_id="worker-one", lease_seconds=30)
    assert owner == "worker-one"
    detail = repository.get(run["runId"], details=False)
    assert detail["executionPhase"] == "starting"
    assert detail["leaseExpiresAt"] is not None
    assert detail["lastHeartbeatAt"] is not None
    assert repository.renew_lease(
        run["runId"], owner, phase="model_request", lease_seconds=30
    ) is True
    assert repository.renew_lease(run["runId"], "worker-two") is False
    with pytest.raises(Conflict, match="cannot be started"):
        repository.start(run["runId"], owner_id="worker-two")
    detail = repository.get(run["runId"], details=False)
    assert detail["executionPhase"] == "model_request"
    events = repository.events(run["runId"], 0, 100)["events"]
    assert [event["type"] for event in events].count("run.lease_acquired") == 1
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
    assert versions == [1, 2, 3, 4, 5, 6, 7]
    assert store._conn.execute(
        "SELECT title_is_generated FROM conversation_instances WHERE id='A'"
    ).fetchone()[0] == 0
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
    )] == [1, 2, 3, 4, 5, 6, 7]
    assert [row[0] for row in store._conn.execute(
        "SELECT version FROM runtime_schema_migrations ORDER BY version"
    )] == [1, 2, 3]
    assert {
        "lease_owner", "lease_expires_at", "last_heartbeat_at", "execution_phase"
    } <= {row[1] for row in store._conn.execute("PRAGMA table_info(agent_runs)")}
    assert "effect_key" in {
        row[1] for row in store._conn.execute("PRAGMA table_info(tool_calls)")
    }
    assert store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_effects'"
    ).fetchone()
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
        lambda timeout, **_kwargs: original_client(timeout=timeout, transport=httpx.MockTransport(handler)),
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
        lambda timeout, **_kwargs: original_client(
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
        # Recovery occurred while an upstream model request was in flight. Its
        # completion is therefore unknown and must never be replayed or
        # presented as an ordinary pre-execution interruption.
        assert response.json()["code"] == "modelOutcomeUnknown"
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


def test_runtime_v2_retry_persists_attempt_lineage():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([
        ModelTurn(final_answer="first"), ModelTurn(final_answer="second"),
    ]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        first = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        ).json()
        retried = client.post(f"/api/v1/runs/{first['runId']}/retry", json={
            "idempotencyKey": "retry-key",
        })
        assert retried.status_code == 201
        second = retried.json()
        assert second["status"] == "completed"
        assert second["parentRunId"] == first["runId"]
        assert second["rootRunId"] == first["runId"]
        assert second["attemptNumber"] == 2
        assert client.get(f"/api/v1/runs/{first['runId']}").json()["attemptNumber"] == 1
        events = client.get(f"/api/v1/runs/{second['runId']}/events").json()["events"]
        assert any(event["type"] == "run.retry_created" for event in events)
    store.close()


def test_retry_reuses_completed_side_effect_in_root_lineage_exactly_once():
    executions = 0

    def proposal(arguments):
        nonlocal executions
        executions += 1
        return {**arguments, "filesystemChanged": False}

    arguments = {"path": "src/safe.py", "patch": "+reliable"}
    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    engineering = EngineeringRepository(store._conn, store._lock)
    service = AgentRuntimeService(
        store,
        repository,
        ScriptedMockAgentAdapter([
            ModelTurn(tool_name="propose_patch", tool_arguments=arguments),
            ModelTurn(tool_name="propose_patch", tool_arguments=arguments),
        ]),
        ToolRegistry([Tool(
            name="propose_patch", version="1.0.0", description="Proposal only.",
            schema={"type": "object"}, execute=proposal, side_effect="artifact",
        )]),
        engineering=engineering,
    )

    first = service.execute(graph["workflowId"], "A", request(0, "first-effect"))
    first_approval = first["approvalRequests"][0]["approvalId"]
    completed = service.decide_approval(first["runId"], first_approval, "approved")
    assert completed["status"] == "completed"
    assert executions == 1
    assert len(engineering.list_artifacts(graph["workflowId"])) == 1

    retry = service.retry(first["runId"], {"idempotencyKey": "same-effect-retry"})
    assert retry["status"] == "awaiting_approval"
    retry_approval = retry["approvalRequests"][0]["approvalId"]
    reused = service.decide_approval(retry["runId"], retry_approval, "approved")
    assert reused["status"] == "completed"
    assert executions == 1
    assert len(engineering.list_artifacts(graph["workflowId"])) == 1
    retry_events = repository.events(retry["runId"], 0, 100)["events"]
    assert [event["type"] for event in retry_events].count("tool.reused") == 1
    first_key = repository.get(first["runId"])["toolCalls"][0]["effectKey"]
    retry_key = repository.get(retry["runId"])["toolCalls"][0]["effectKey"]
    assert retry_key == first_key
    store.close()


def test_runtime_v2_cancel_is_durable_and_late_model_answer_is_discarded():
    entered, release = threading.Event(), threading.Event()

    class BlockingModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "test", "model": "blocking"}

        def next(self, messages, tools):
            del messages, tools
            entered.set()
            assert release.wait(2)
            return ModelTurn(final_answer="must be discarded", usage={
                "inputTokens": 100, "outputTokens": 3,
                "cachedInputTokens": 60, "uncachedInputTokens": 40,
                "cacheStatus": "reported",
            })

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    service = AgentRuntimeService(store, repository, BlockingModel(), runtime_registry())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.execute, graph["workflowId"], "A", request(0))
        assert entered.wait(1)
        run_id = repository.list(graph["workflowId"], "A")[0]["runId"]
        assert service.cancel(run_id)["status"] == "cancelling"
        release.set()
        result = future.result(timeout=2)
    assert result["status"] == "cancelled"
    assert repository.get(run_id)["errorCode"] == "runCancelled"
    metrics = repository.get(run_id)["metrics"]
    assert metrics["modelStepCount"] == 1
    assert metrics["inputTokens"] == 100
    assert metrics["outputTokens"] == 3
    assert metrics["cachedInputTokens"] == 60
    assert metrics["uncachedInputTokens"] == 40
    assert metrics["cacheReuseRatio"] == pytest.approx(0.6)
    assert metrics["cacheCoverage"] == pytest.approx(1.0)
    assert metrics["cacheStatus"] == "reported"
    assert store.list_messages(graph["workflowId"], "A", scope="local")["messages"] == []
    types = [event["type"] for event in repository.events(run_id, 0, 100)["events"]]
    assert types.count("run.cancel_requested") == 1
    assert types.count("run.cancelled") == 1
    store.close()


def test_approval_never_executes_an_unallowlisted_side_effect_tool():
    invoked = False

    def danger(arguments):
        nonlocal invoked
        invoked = True
        return {"changed": True, **arguments}

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    service = AgentRuntimeService(
        store,
        repository,
        ScriptedMockAgentAdapter([ModelTurn(tool_name="danger", tool_arguments={})]),
        ToolRegistry([Tool(
            name="danger", version="1.0.0", description="Must never run.",
            schema={"type": "object", "additionalProperties": False},
            execute=danger, side_effect="workspace_write",
        )]),
        engineering=EngineeringRepository(store._conn, store._lock),
    )
    waiting = service.execute(graph["workflowId"], "A", request(0))
    approval = waiting["approvalRequests"][0]
    with pytest.raises(Exception) as caught:
        service.decide_approval(waiting["runId"], approval["approvalId"], "approved")
    assert getattr(caught.value, "code", None) == "unknownTool"
    assert invoked is False
    assert repository.get(waiting["runId"])["status"] == "failed"
    store.close()


def test_approved_side_effect_and_cancel_race_always_reaches_a_terminal_state():
    entered, release = threading.Event(), threading.Event()

    def blocking_proposal(arguments):
        entered.set()
        assert release.wait(2)
        return {**arguments, "filesystemChanged": False}

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    service = AgentRuntimeService(
        store,
        repository,
        ScriptedMockAgentAdapter([ModelTurn(
            tool_name="propose_patch",
            tool_arguments={"path": "src/a.py", "patch": "+safe"},
        )]),
        ToolRegistry([Tool(
            name="propose_patch", version="1.0.0", description="Proposal only.",
            schema={"type": "object"}, execute=blocking_proposal, side_effect="artifact",
        )]),
        engineering=EngineeringRepository(store._conn, store._lock),
    )
    waiting = service.execute(graph["workflowId"], "A", request(0))
    approval_id = waiting["approvalRequests"][0]["approvalId"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        approval_future = pool.submit(
            service.decide_approval, waiting["runId"], approval_id, "approved"
        )
        assert entered.wait(1)
        assert service.cancel(waiting["runId"])["status"] == "cancelling"
        release.set()
        result = approval_future.result(timeout=2)
    assert result["status"] == "cancelled"
    detail = repository.get(waiting["runId"])
    assert detail["status"] == "cancelled"
    assert len(detail["artifacts"]) == 1
    assert detail["artifacts"][0]["metadata"]["filesystemChanged"] is False
    assert detail["toolCalls"][0]["status"] == "completed"
    store.close()


def test_cancel_before_approval_prevents_the_side_effect_and_closes_the_request():
    invoked = False

    def proposal(arguments):
        nonlocal invoked
        invoked = True
        return {**arguments, "filesystemChanged": False}

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    service = AgentRuntimeService(
        store,
        repository,
        ScriptedMockAgentAdapter([ModelTurn(
            tool_name="propose_patch", tool_arguments={"path": "x.py", "patch": "+safe"},
        )]),
        ToolRegistry([Tool(
            name="propose_patch", version="1.0.0", description="Proposal only.",
            schema={"type": "object"}, execute=proposal, side_effect="artifact",
        )]),
        engineering=EngineeringRepository(store._conn, store._lock),
    )
    waiting = service.execute(graph["workflowId"], "A", request(0))
    approval_id = waiting["approvalRequests"][0]["approvalId"]
    cancelled = service.cancel(waiting["runId"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["approvalRequests"][0]["status"] == "rejected"
    with pytest.raises(Exception):
        service.decide_approval(waiting["runId"], approval_id, "approved")
    assert invoked is False
    detail = repository.get(waiting["runId"])
    assert detail["status"] == "cancelled"
    assert detail["artifacts"] == []
    assert detail["toolCalls"][0]["status"] == "cancelled"
    store.close()


def test_cancel_winning_before_the_side_effect_claim_creates_no_artifact():
    invoked = False
    claim_entered, release_claim = threading.Event(), threading.Event()

    def proposal(arguments):
        nonlocal invoked
        invoked = True
        return {**arguments, "filesystemChanged": False}

    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    repository = AgentRunRepository(store._conn, store._lock)
    original_claim = repository.claim_approved_tool

    def delayed_claim(run_id, approval_id, **kwargs):
        claim_entered.set()
        assert release_claim.wait(2)
        return original_claim(run_id, approval_id, **kwargs)

    repository.claim_approved_tool = delayed_claim  # type: ignore[method-assign]
    service = AgentRuntimeService(
        store,
        repository,
        ScriptedMockAgentAdapter([ModelTurn(
            tool_name="propose_patch", tool_arguments={"path": "x.py", "patch": "+safe"},
        )]),
        ToolRegistry([Tool(
            name="propose_patch", version="1.0.0", description="Proposal only.",
            schema={"type": "object"}, execute=proposal, side_effect="artifact",
        )]),
        engineering=EngineeringRepository(store._conn, store._lock),
    )
    waiting = service.execute(graph["workflowId"], "A", request(0))
    approval_id = waiting["approvalRequests"][0]["approvalId"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        approval_future = pool.submit(
            service.decide_approval, waiting["runId"], approval_id, "approved"
        )
        assert claim_entered.wait(1)
        assert service.cancel(waiting["runId"])["status"] == "cancelling"
        release_claim.set()
        result = approval_future.result(timeout=2)
    assert result["status"] == "cancelled"
    assert invoked is False
    detail = repository.get(waiting["runId"])
    assert detail["artifacts"] == []
    assert detail["toolCalls"][0]["status"] == "cancelled"
    store.close()


def test_patch_proposal_returns_approval_immediately_and_approval_creates_only_artifact():
    store = GraphStore(":memory:")
    proposal = ModelTurn(
        tool_name="propose_patch",
        tool_arguments={"path": "src/example.py", "patch": "--- a/src/example.py\n+++ b/src/example.py"},
        tool_call_id="provider-proposal-1",
    )
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([proposal]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        body = request(revision)
        first = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=body)
        assert first.status_code == 201
        waiting = first.json()
        assert waiting["status"] == "awaiting_approval"
        assert len(waiting["approvalRequests"]) == 1
        approval = waiting["approvalRequests"][0]
        replay = client.post(f"/api/v1/workflows/{wf}/instances/A/runs", json=body)
        assert replay.status_code == 201
        assert replay.json()["status"] == "awaiting_approval"
        assert replay.json()["runId"] == waiting["runId"]

        approved = client.post(
            f"/api/v1/runs/{waiting['runId']}/approvals/{approval['approvalId']}/decision",
            json={"decision": "approved"},
        )
        assert approved.status_code == 200
        completed = approved.json()
        assert completed["status"] == "completed"
        assert completed["approvalRequests"][0]["status"] == "approved"
        assert completed["artifacts"][0]["kind"] == "patch"
        assert completed["artifacts"][0]["metadata"]["filesystemChanged"] is False
        result = completed["toolResults"][0]["output"]
        assert result["filesystemChanged"] is False
        assert result["artifactId"] == completed["artifacts"][0]["artifactId"]
        assert client.post(
            f"/api/v1/runs/{waiting['runId']}/approvals/{approval['approvalId']}/decision",
            json={"decision": "approved"},
        ).json()["artifacts"] == completed["artifacts"]
    store.close()


def test_rejected_approval_cancels_run_without_artifact():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(
        tool_name="propose_patch", tool_arguments={"path": "x.py", "patch": "+safe"},
    )]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        waiting = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        ).json()
        approval_id = waiting["approvalRequests"][0]["approvalId"]
        rejected = client.post(
            f"/api/v1/runs/{waiting['runId']}/approvals/{approval_id}/decision",
            json={"decision": "rejected"},
        ).json()
        assert rejected["status"] == "cancelled"
        assert rejected["errorCode"] == "approvalRejected"
        assert rejected["artifacts"] == []
    store.close()


def test_approved_artifact_survives_route_revision_conflict_and_run_fails_terminally():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(
        tool_name="propose_patch", tool_arguments={"path": "x.py", "patch": "+proposal"},
    )]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        waiting = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        ).json()
        store.append_message(wf, "A", role="user", content="route changed")
        approval_id = waiting["approvalRequests"][0]["approvalId"]
        response = client.post(
            f"/api/v1/runs/{waiting['runId']}/approvals/{approval_id}/decision",
            json={"decision": "approved"},
        )
        assert response.status_code == 409
        detail = client.get(f"/api/v1/runs/{waiting['runId']}").json()
        assert detail["status"] == "failed"
        assert detail["errorCode"] == "runRevisionConflict"
        assert len(detail["artifacts"]) == 1
    store.close()


def test_workspace_tools_require_explicit_root_and_reject_escape_or_secrets(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("needle = 42\n", encoding="utf-8")
    (tmp_path / ".env").write_text("needle=secret\n", encoding="utf-8")
    sensitive = [
        ".env.test", ".envrc", ".npmrc", "id_rsa", "private.pem",
        "credentials.json", "token.json", "model-settings.json", "workspace.db-wal",
        "workspace.db.backup", ".envrc.local", ".env~", "id_rsa.bak",
        "id_ed25519_sk", "client.ppk", "signing.keystore", "archive.pkcs12",
        "application_default_credentials.json", "service-account.json",
    ]
    for name in sensitive:
        (tmp_path / name).write_text("needle=secret\n", encoding="utf-8")
    assert runtime_registry().resolve("read_file") is None
    registry = runtime_registry(tmp_path)
    reader, search = registry.resolve("read_file"), registry.resolve("workspace_search")
    assert reader is not None and search is not None
    registry.validate(reader, {"path": "src/main.py"})
    assert reader.execute({"path": "src/main.py"})["content"].splitlines() == ["needle = 42"]
    found = search.execute({"query": "needle", "maxResults": 10})
    assert [item["path"] for item in found["matches"]] == ["src/main.py"]
    with pytest.raises(ValueError):
        registry.validate(reader, {"path": "../outside.txt"})
    with pytest.raises(ValueError):
        registry.validate(reader, {"path": ".env"})
    for name in sensitive:
        with pytest.raises(ValueError):
            registry.validate(reader, {"path": name})


def test_model_usage_and_cache_metrics_are_persisted():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([ModelTurn(
        final_answer="done", usage={"inputTokens": 100, "outputTokens": 8,
                                    "cachedInputTokens": 60, "uncachedInputTokens": 40},
    )]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        run = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        ).json()
        metrics = client.get(f"/api/v1/runs/{run['runId']}").json()["metrics"]
        assert metrics["inputTokens"] == 100
        assert metrics["outputTokens"] == 8
        assert metrics["cachedInputTokens"] == 60
        assert metrics["uncachedInputTokens"] == 40
        assert metrics["cacheReuseRatio"] == pytest.approx(0.6)
        assert metrics["cacheCoverage"] == pytest.approx(1.0)
        assert metrics["cacheStatus"] == "reported"
    store.close()


def test_cache_ratio_uses_only_reported_calls_and_coverage_uses_all_model_steps():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([
        ModelTurn(tool_name="safe_calculator", tool_arguments={"expression": "2+2"},
                  usage={"inputTokens": 100, "outputTokens": 3, "cachedInputTokens": 50,
                         "uncachedInputTokens": 50, "cacheStatus": "reported"}),
        ModelTurn(final_answer="4", usage={"inputTokens": 50, "outputTokens": 1,
                                            "cachedInputTokens": None,
                                            "uncachedInputTokens": None,
                                            "cacheStatus": "unsupported"}),
    ]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        run = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        ).json()
        metrics = client.get(f"/api/v1/runs/{run['runId']}").json()["metrics"]
        assert metrics["inputTokens"] == 150
        assert metrics["outputTokens"] == 4
        assert metrics["cachedInputTokens"] == 50
        assert metrics["uncachedInputTokens"] == 50
        assert metrics["cacheReuseRatio"] == pytest.approx(0.5)
        assert metrics["cacheCoverage"] == pytest.approx(0.5)
        assert metrics["cacheStatus"] == "reported"
    store.close()


def test_cache_ratio_never_cross_pairs_partial_usage_from_different_calls():
    store = GraphStore(":memory:")
    app = create_app(store, agent_model=ScriptedMockAgentAdapter([
        ModelTurn(
            tool_name="safe_calculator", tool_arguments={"expression": "2+2"},
            usage={"inputTokens": 100, "outputTokens": 3, "cachedInputTokens": 50,
                   "uncachedInputTokens": 50, "cacheStatus": "reported"},
        ),
        ModelTurn(
            final_answer="4",
            usage={"inputTokens": None, "outputTokens": 1, "cachedInputTokens": 100,
                   "uncachedInputTokens": None, "cacheStatus": "reported"},
        ),
    ]))
    with TestClient(app) as client:
        wf, revision = workflow(client)
        run = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        ).json()
        metrics = client.get(f"/api/v1/runs/{run['runId']}").json()["metrics"]
        # Both reported cached counts remain auditable, but only the complete
        # first call is eligible for a ratio denominator.
        assert metrics["cachedInputTokens"] == 150
        assert metrics["cacheReuseRatio"] == pytest.approx(0.5)
        assert metrics["cacheCoverage"] == pytest.approx(0.5)
        assert 0 <= metrics["cacheReuseRatio"] <= 1
    store.close()


def test_recovery_preserves_approval_but_converges_cancelling_to_cancelled():
    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    wf = graph["workflowId"]
    repo = AgentRunRepository(store._conn, store._lock)
    waiting, _ = repo.create(workflow_id=wf, instance_id="A", request=request(0, "approval"),
                             context={"messages": []}, model_snapshot={})
    repo.start(waiting["runId"])
    repo.request_approval(waiting["runId"], 1, name="propose_patch", version="1.0.0",
                          arguments={"path": "x.py", "patch": "+x"}, side_effect="artifact")
    cancelling, _ = repo.create(workflow_id=wf, instance_id="A", request=request(0, "cancel"),
                                context={"messages": []}, model_snapshot={})
    repo.start(cancelling["runId"])
    assert repo.request_cancel(cancelling["runId"])["status"] == "cancelling"
    assert repo.recover_interrupted() == 1
    assert repo.get(waiting["runId"])["status"] == "awaiting_approval"
    assert repo.get(cancelling["runId"])["status"] == "cancelled"
    store.close()


def test_recovery_journals_unfinished_approved_tools_without_replaying_them():
    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    wf = graph["workflowId"]
    repo = AgentRunRepository(store._conn, store._lock)
    run, _ = repo.create(
        workflow_id=wf,
        instance_id="A",
        request=request(0, "executing-recovery"),
        context={"messages": []},
        model_snapshot={},
    )
    repo.start(run["runId"])
    waiting = repo.request_approval(
        run["runId"], 1, name="propose_patch", version="1.0.0",
        arguments={"path": "x.py", "patch": "+x"}, side_effect="artifact",
    )
    approval_id = waiting["approvalRequests"][0]["approvalId"]
    repo.decide_approval(run["runId"], approval_id, "approved")
    repo.claim_approved_tool(run["runId"], approval_id)

    preclaim, _ = repo.create(
        workflow_id=wf,
        instance_id="A",
        request=request(0, "approved-before-claim-recovery"),
        context={"messages": []},
        model_snapshot={},
    )
    repo.start(preclaim["runId"])
    preclaim_waiting = repo.request_approval(
        preclaim["runId"], 1, name="propose_patch", version="1.0.0",
        arguments={"path": "y.py", "patch": "+y"}, side_effect="artifact",
    )
    repo.decide_approval(
        preclaim["runId"], preclaim_waiting["approvalRequests"][0]["approvalId"],
        "approved",
    )

    cancelling, _ = repo.create(
        workflow_id=wf,
        instance_id="A",
        request=request(0, "cancel-during-execution-recovery"),
        context={"messages": []},
        model_snapshot={},
    )
    repo.start(cancelling["runId"])
    cancelling_waiting = repo.request_approval(
        cancelling["runId"], 1, name="propose_patch", version="1.0.0",
        arguments={"path": "z.py", "patch": "+z"}, side_effect="artifact",
    )
    cancelling_approval = cancelling_waiting["approvalRequests"][0]["approvalId"]
    repo.decide_approval(cancelling["runId"], cancelling_approval, "approved")
    repo.claim_approved_tool(cancelling["runId"], cancelling_approval)
    assert repo.request_cancel(cancelling["runId"])["status"] == "cancelling"

    assert repo.recover_interrupted() == 3
    detail = repo.get(run["runId"])
    assert detail["status"] == "interrupted"
    assert detail["errorCode"] == "toolOutcomeUnknown"
    assert detail["toolCalls"][0]["status"] == "interrupted"
    assert detail["steps"][0]["status"] == "interrupted"
    events = repo.events(run["runId"], 0, 100)["events"]
    interrupted = next(event for event in events if event["type"] == "tool.interrupted")
    assert interrupted["payload"]["executionOutcome"] == "unknown"
    preclaim_detail = repo.get(preclaim["runId"])
    assert preclaim_detail["status"] == "interrupted"
    assert preclaim_detail["errorCode"] == "runInterrupted"
    assert preclaim_detail["toolCalls"][0]["status"] == "interrupted"
    assert next(
        event for event in repo.events(preclaim["runId"], 0, 100)["events"]
        if event["type"] == "tool.interrupted"
    )["payload"]["executionOutcome"] == "notStarted"
    cancelling_detail = repo.get(cancelling["runId"])
    assert cancelling_detail["status"] == "cancelled"
    assert cancelling_detail["toolCalls"][0]["status"] == "interrupted"
    assert next(
        event for event in repo.events(cancelling["runId"], 0, 100)["events"]
        if event["type"] == "tool.interrupted"
    )["payload"]["executionOutcome"] == "unknown"
    effect = store._conn.execute(
        "SELECT status,error_code FROM tool_effects WHERE effect_key=?",
        (detail["toolCalls"][0]["effectKey"],),
    ).fetchone()
    assert tuple(effect) == ("interrupted", "toolOutcomeUnknown")
    store.close()


def test_retry_blocks_an_interrupted_side_effect_with_unknown_outcome():
    invoked = False

    def proposal(arguments):
        nonlocal invoked
        invoked = True
        return {**arguments, "filesystemChanged": False}

    arguments = {"path": "unknown.py", "patch": "+maybe"}
    store = GraphStore(":memory:")
    graph = store.create_workflow(name="Agent", root_title="A", root_instance_id="A")
    wf = graph["workflowId"]
    repository = AgentRunRepository(store._conn, store._lock)
    source, _ = repository.create(
        workflow_id=wf,
        instance_id="A",
        request=request(0, "unknown-source"),
        context={"messages": []},
        model_snapshot={"provider": "test", "model": "recovery"},
    )
    repository.start(source["runId"])
    waiting = repository.request_approval(
        source["runId"], 1, name="propose_patch", version="1.0.0",
        arguments=arguments, side_effect="artifact",
    )
    approval_id = waiting["approvalRequests"][0]["approvalId"]
    repository.decide_approval(source["runId"], approval_id, "approved")
    repository.claim_approved_tool(source["runId"], approval_id)
    assert repository.recover_interrupted() == 1

    service = AgentRuntimeService(
        store,
        repository,
        ScriptedMockAgentAdapter([
            ModelTurn(tool_name="propose_patch", tool_arguments=arguments),
        ]),
        ToolRegistry([Tool(
            name="propose_patch", version="1.0.0", description="Proposal only.",
            schema={"type": "object"}, execute=proposal, side_effect="artifact",
        )]),
        engineering=EngineeringRepository(store._conn, store._lock),
    )
    retry = service.retry(source["runId"], {"idempotencyKey": "unknown-retry"})
    retry_approval = retry["approvalRequests"][0]["approvalId"]
    with pytest.raises(Exception) as caught:
        service.decide_approval(retry["runId"], retry_approval, "approved")
    assert getattr(caught.value, "code", None) == "toolOutcomeUnknown"
    assert repository.get(retry["runId"])["errorCode"] == "toolOutcomeUnknown"
    assert invoked is False
    assert EngineeringRepository(store._conn, store._lock).list_artifacts(wf) == []
    store.close()


def test_cache_aware_prompt_has_stable_prefix_dynamic_parent_memory_and_sibling_isolation():
    captured: list[tuple[list[dict], list[dict]]] = []

    class CaptureModel:
        def bind(self):
            return self

        def snapshot(self):
            return {"provider": "test", "model": "capture", "systemPrompt": "Stable host rule"}

        def next(self, messages, tools):
            captured.append((json.loads(json.dumps(messages)), json.loads(json.dumps(tools))))
            return ModelTurn(final_answer=f"answer-{len(captured)}")

    store = GraphStore(":memory:")
    app = create_app(store, agent_model=CaptureModel())
    with TestClient(app) as client:
        wf, _ = workflow(client)
        store.append_message(wf, "A", role="user", content="A shared")
        store.fork(wf, "A", title="B", instance_id="B", initial_message="B shared")
        store.fork(wf, "B", title="C", instance_id="C", initial_message="C private")
        store.fork(wf, "B", title="E", instance_id="E", initial_message="E private")
        # Parent updates after both forks are live memory, not frozen checkpoint data.
        store.append_message(wf, "B", role="assistant", content="B latest")
        for instance_id, key in (("C", "cache-c"), ("E", "cache-e")):
            revision = store.list_messages(wf, instance_id, scope="local")["contentRevision"]
            client.post(
                f"/api/v1/workflows/{wf}/instances/{instance_id}/runs",
                json=request(revision, key),
            ).raise_for_status()

        c_messages, c_tools = captured[0]
        e_messages, e_tools = captured[1]
        assert c_messages[0]["role"] == "system"
        assert c_messages[0]["content"].count("Stable host rule") == 1
        assert [tool["name"] for tool in c_tools] == sorted(tool["name"] for tool in c_tools)
        assert c_tools == e_tools
        c_content = [message["content"] for message in c_messages]
        e_content = [message["content"] for message in e_messages]
        assert c_content[1:4] == e_content[1:4] == ["A shared", "B shared", "B latest"]
        assert c_content[4] == "C private" and e_content[4] == "E private"
        assert "E private" not in json.dumps(c_messages)
        assert "C private" not in json.dumps(e_messages)
        # Volatile request identity and UI/database state never enter provider prompt text.
        serialized = json.dumps(c_messages) + json.dumps(c_tools)
        assert "cache-c" not in serialized and "run_" not in serialized

        runs = app.state.agent_runs.list(wf, "C") + app.state.agent_runs.list(wf, "E")
        assert len({run["stablePrefixSha256"] for run in runs}) == 1
        assert all(run["promptLayoutVersion"] == "agent-cache-v3" for run in runs)
    store.close()


def test_usage_parser_normalizes_openai_and_deepseek_cache_fields():
    openai = _usage({"usage": {
        "prompt_tokens": 120, "completion_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 90},
    }})
    assert openai == {"inputTokens": 120, "outputTokens": 7,
                      "cachedInputTokens": 90, "uncachedInputTokens": 30,
                      "cacheStatus": "reported"}
    responses = _usage({"usage": {
        "input_tokens": 90, "output_tokens": 3,
        "input_tokens_details": {"cached_tokens": 40},
    }})
    assert responses == {"inputTokens": 90, "outputTokens": 3,
                          "cachedInputTokens": 40, "uncachedInputTokens": 50,
                          "cacheStatus": "reported"}
    deepseek = _usage({"usage": {
        "completion_tokens": 5, "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    }})
    assert deepseek == {"inputTokens": 100, "outputTokens": 5,
                        "cachedInputTokens": 80, "uncachedInputTokens": 20,
                        "cacheStatus": "reported"}
    assert _usage({"usage": {"prompt_tokens": 10, "completion_tokens": 2}})[
        "cacheStatus"
    ] == "unsupported"
    assert _usage({}) is None
    invalid = _usage({"usage": {"prompt_tokens": 10,
                                 "prompt_tokens_details": {"cached_tokens": 20}}})
    assert invalid is not None and invalid["cacheStatus"] == "invalid"
    assert invalid["cachedInputTokens"] is None
    inconsistent_deepseek = _usage({"usage": {
        "prompt_tokens": 10, "prompt_cache_hit_tokens": 8,
        "prompt_cache_miss_tokens": 3,
    }})
    assert inconsistent_deepseek is not None
    assert inconsistent_deepseek["cacheStatus"] == "invalid"
    conflicting_formats = _usage({"usage": {
        "prompt_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 7},
        "prompt_cache_hit_tokens": 6,
        "prompt_cache_miss_tokens": 4,
    }})
    assert conflicting_formats is not None
    assert conflicting_formats["cacheStatus"] == "invalid"


def test_openai_adapter_does_not_duplicate_builder_system_policy(monkeypatch):
    captured: dict[str, Any] = {}
    original_client = httpx.Client

    def handler(request_: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request_.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": "done"}}]})

    monkeypatch.setattr(
        "agent_runtime.adapters.httpx.Client",
        lambda timeout, **_kwargs: original_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )
    adapter = OpenAICompatibleAgentAdapter(lambda: OpenAICompatibleLLM(
        base_url="https://provider.test/v1", model="m", system_prompt="host rule",
    ))
    adapter.next([{"role": "system", "content": "policy + host rule"},
                  {"role": "user", "content": "request"}], [])
    assert captured["messages"] == [
        {"role": "system", "content": "policy + host rule"},
        {"role": "user", "content": "request"},
    ]


def test_protocol_failure_still_journals_provider_cache_usage(monkeypatch):
    original_client = httpx.Client

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "truncated response"},
            }],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 12},
            },
        })

    monkeypatch.setattr(
        "agent_runtime.adapters.httpx.Client",
        lambda timeout, **_kwargs: original_client(
            timeout=timeout, transport=httpx.MockTransport(handler)
        ),
    )
    store = GraphStore(":memory:")
    adapter = OpenAICompatibleAgentAdapter(lambda: OpenAICompatibleLLM(
        base_url="https://provider.test/v1", model="cache-aware-model",
    ))
    app = create_app(store, agent_model=adapter)
    with TestClient(app) as client:
        wf, revision = workflow(client)
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/runs", json=request(revision)
        )
        assert response.status_code == 502
        assert response.json()["code"] == "modelProtocolError"
        run = app.state.agent_runs.list(wf, "A")[0]
        detail = client.get(f"/api/v1/runs/{run['runId']}").json()
        assert detail["status"] == "failed"
        assert detail["steps"][0]["status"] == "failed"
        expected_metrics = {
            "modelStepCount": 1,
            "inputTokens": 20,
            "outputTokens": 3,
            "cachedInputTokens": 12,
            "uncachedInputTokens": 8,
            "cacheReuseRatio": pytest.approx(0.6),
            "cacheCoverage": pytest.approx(1.0),
            "cacheStatus": "reported",
        }
        for key, expected in expected_metrics.items():
            assert detail["metrics"][key] == expected
    store.close()
