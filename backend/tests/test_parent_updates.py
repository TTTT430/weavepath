import pytest
from fastapi.testclient import TestClient

from agent_runtime import AgentRunRepository, AgentRuntimeService, ModelTurn, runtime_registry
from agent_runtime.parent_updates import LAYOUT_VERSION, STRATEGY, plan_order
from agent_runtime.service import AgentRunError
from api.app import create_app
from graph_core import GraphStore


class CaptureModel:
    def __init__(self):
        self.calls = []
        self.cached = 0
        self.on_turn = None

    def bind(self):
        return self

    def snapshot(self):
        return {"model": "test", "provider": "test"}

    def next(self, messages, tools):
        self.calls.append(messages.copy())
        if self.on_turn:
            self.on_turn()
        return ModelTurn(final_answer="answer", usage={"inputTokens": 200, "cachedInputTokens": self.cached})


@pytest.fixture
def workspace():
    graph = GraphStore(":memory:")
    workflow = graph.create_workflow(name="Test", root_title="A", root_instance_id="A")["workflowId"]
    graph.append_message(workflow, "A", role="user", content="base")
    graph.fork(workflow, "A", instance_id="B", initial_message="shared")
    graph.fork(workflow, "B", instance_id="C", initial_message="private C")
    graph.fork(workflow, "B", instance_id="E", initial_message="private E")
    try:
        yield graph, workflow
    finally:
        graph.close()


def request(graph, workflow, key, node="C"):
    return {
        "objective": "Evaluate the design", "constraints": [], "deliverables": [], "acceptanceChecks": [],
        "expectedContentRevision": graph.list_messages(workflow, node)["contentRevision"],
        "idempotencyKey": key, "contextStrategy": STRATEGY,
    }


def test_opt_in_retains_order_provenance_and_sibling_isolation(workspace):
    graph, workflow = workspace
    model = CaptureModel()
    with TestClient(create_app(graph, agent_model=model)) as client:
        def run(key, node="C"):
            path = f"/api/v1/workflows/{workflow}/instances/{node}/runs"
            return client.post(path, json=request(graph, workflow, key, node)).raise_for_status().json()

        first = run("seed")
        run("sibling-seed", "E")
        root = graph.append_message(workflow, "A", role="tool", content="reference")
        update = graph.append_message(workflow, "B", role="user", content="must work offline")
        model.cached = 150
        second = run("update")
        assert second["contextAssembly"]["baseRunId"] == first["runId"]
        assert model.calls[2][:4] == model.calls[0][:4]
        assert "Historical tool output" in model.calls[2][5]["content"]
        assert '"originalRole": "tool"' in model.calls[2][5]["content"]
        assert "must work offline" in model.calls[2][6]["content"]
        added = second["contextAssembly"]["newParentUpdates"]
        assert [row["messageId"] for row in added] == [root["id"], update["id"]]
        assert all(row["firstIncludedRunId"] == second["runId"] for row in added)
        model.cached = 0
        graph.rename_instance(
            workflow, "B", title="Renamed parent",
            expected_revision=graph.get_graph(workflow)["graphRevision"],
        )
        third = run("continue")
        assert model.calls[3][:7] == model.calls[2][:7]
        assert third["contextAssembly"]["newParentUpdates"] == []
        sibling = run("sibling-update", "E")
        assert "private C" not in str(model.calls[-1])
        assert sibling["contextAssembly"]["newParentUpdates"][1]["firstIncludedRunId"] == sibling["runId"]


def test_queued_withdrawal_and_edit_rebuild(workspace):
    graph, workflow = workspace
    model = CaptureModel()
    repo = AgentRunRepository(graph._conn, graph._lock)
    service = AgentRuntimeService(graph, repo, model, runtime_registry())
    old = graph.append_message(workflow, "B", role="assistant", content="obsolete")
    first = service.execute(workflow, "C", request(graph, workflow, "seed"))
    queued = service.enqueue(workflow, "C", request(graph, workflow, "queued"), lambda _: None)
    shared = graph.list_messages(workflow, "B", "local")
    graph.commit_latest_local_user_edit(
        workflow, "B", shared["messages"][0]["id"], content="offline only",
        expected_content_revision=shared["contentRevision"],
    )
    with pytest.raises(AgentRunError, match="no longer active") as exc:
        service.resume_queued(queued["runId"])
    assert exc.value.code == "runRevisionConflict"
    assert len(model.calls) == 1
    retried = service.retry(queued["runId"], {"idempotencyKey": "retry"})
    assert retried["contextAssembly"]["baseRunId"] == first["runId"]
    assert retried["contextAssembly"]["rebuildReason"] == "messageRemoved"
    assert old["content"] not in str(model.calls[-1])
    assert "offline only" in str(model.calls[-1])


def test_recovery_uses_frozen_order_and_budget(tmp_path, monkeypatch):
    path = tmp_path / "context.db"
    graph = GraphStore(path)
    workflow = graph.create_workflow(name="Test", root_title="A", root_instance_id="A")["workflowId"]
    graph.fork(workflow, "A", instance_id="C", initial_message="child")
    model = CaptureModel()
    repo = AgentRunRepository(graph._conn, graph._lock)
    service = AgentRuntimeService(graph, repo, model, runtime_registry())
    service.execute(workflow, "C", request(graph, workflow, "seed"))
    graph.append_message(workflow, "A", role="user", content="new constraint")
    queued = service.enqueue(workflow, "C", request(graph, workflow, "queued"), lambda _: None)
    graph.close()
    monkeypatch.setenv("WEAVEPATH_CONTEXT_BUDGET_CHARS", "16000")
    graph = GraphStore(path)
    try:
        repo = AgentRunRepository(graph._conn, graph._lock)
        service = AgentRuntimeService(graph, repo, model, runtime_registry())
        resumed = service.resume_queued(queued["runId"])
        assert resumed["modelRequestSha256"] == queued["modelRequestSha256"]
        assert model.calls[-1][1]["content"] == "child"
        assert "new constraint" in model.calls[-1][-2]["content"]
    finally:
        graph.close()


@pytest.mark.parametrize(("field", "value"), [("content", "edited"), ("role", "assistant")])
def test_same_id_changed_input_rebuilds(field, value):
    before = [{"id": 1, "sourceInstanceId": "A", "role": "user", "content": "original"}]
    order = plan_order(before, ["A", "C"], None, 16000)
    baseline = ("run", {"messages": before, "memoryRoute": [{"instanceId": "A"}, {"instanceId": "C"}],
                        "promptLayoutVersion": LAYOUT_VERSION, "contextPlacement": order})
    after = [{**before[0], field: value}]
    assert plan_order(after, ["A", "C"], baseline, 16000)["rebuildReason"] == "messageChanged"


def test_cancelled_and_unacknowledged_snapshots_are_not_baselines(workspace):
    graph, workflow = workspace
    model = CaptureModel()
    repo = AgentRunRepository(graph._conn, graph._lock)
    service = AgentRuntimeService(graph, repo, model, runtime_registry())
    first = service.execute(workflow, "C", request(graph, workflow, "seed"))
    graph.append_message(workflow, "B", role="user", content="updated constraint")
    body = request(graph, workflow, "once")
    queued = service.enqueue(workflow, "C", body, lambda _: None)
    assert service.enqueue(workflow, "C", body, lambda _: pytest.fail("duplicate dispatch"))["runId"] == queued["runId"]
    service.cancel(queued["runId"])
    assert repo.confirmed_context_baseline(workflow, "C")[0] == first["runId"]
    next_run = service.execute(workflow, "C", request(graph, workflow, "next"))
    assert next_run["contextAssembly"]["baseRunId"] == first["runId"]
    assert next_run["contextAssembly"]["newParentUpdates"][0]["firstIncludedRunId"] == next_run["runId"]


def test_experimental_compaction_rebuilds_in_route_order(workspace, monkeypatch):
    graph, workflow = workspace
    model = CaptureModel()
    repo = AgentRunRepository(graph._conn, graph._lock)
    service = AgentRuntimeService(graph, repo, model, runtime_registry())
    service.execute(workflow, "C", request(graph, workflow, "seed"))
    monkeypatch.setenv("WEAVEPATH_CONTEXT_BUDGET_CHARS", "16000")
    graph.append_message(workflow, "B", role="user", content="new ancestor update")
    for index in range(12):
        graph.append_message(workflow, "C", role="user", content=f"{index} " + "x" * 1500)
    result = service.execute(workflow, "C", request(graph, workflow, "compacted"))
    assert result["compactionPlan"] is not None
    assert result["contextAssembly"]["rebuildReason"] == "compaction"
    assert result["contextAssembly"]["newParentUpdates"] == []
    assert all("Ancestor update" not in message["content"] for message in model.calls[-1][1:])


def test_concurrent_preparations_share_acknowledged_baseline(workspace):
    from concurrent.futures import ThreadPoolExecutor

    graph, workflow = workspace
    model = CaptureModel()
    repo = AgentRunRepository(graph._conn, graph._lock)
    service = AgentRuntimeService(graph, repo, model, runtime_registry())
    first = service.execute(workflow, "C", request(graph, workflow, "seed"))
    update = graph.append_message(workflow, "B", role="user", content="updated constraint")
    with ThreadPoolExecutor(max_workers=2) as pool:
        bodies = [request(graph, workflow, key) for key in ("parallel-1", "parallel-2")]
        futures = [pool.submit(service.enqueue, workflow, "C", body, lambda _: None) for body in bodies]
        runs = [future.result() for future in futures]
    assert len({run["runId"] for run in runs}) == 2
    for run in runs:
        assert run["contextAssembly"]["baseRunId"] == first["runId"]
        assert [row["messageId"] for row in run["contextAssembly"]["newParentUpdates"]] == [update["id"]]
    assert runs[0]["modelRequestSha256"] == runs[1]["modelRequestSha256"]
