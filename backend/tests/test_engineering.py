from fastapi.testclient import TestClient

from agent_runtime import ModelTurn, ScriptedMockAgentAdapter
from api.app import create_app
from graph_core import GraphStore


class CapturingLLM:
    def __init__(self):
        self.messages = []

    def status(self):
        return {"configured": True, "provider": "fake", "model": "test", "reason": None}

    def complete(self, messages):
        self.messages = messages
        return "used approved knowledge"


def _workspace(client: TestClient) -> tuple[str, dict, dict, dict]:
    graph = client.post("/api/v1/workflows", json={
        "name": "Lab", "rootTitle": "A", "rootInstanceId": "A"
    }).json()
    workflow_id = graph["workflowId"]
    b = client.post(f"/api/v1/workflows/{workflow_id}/instances/A/fork", json={
        "title": "B", "instanceId": "B", "initialMessage": "B local",
        "expectedContentRevision": 0, "idempotencyKey": "fork-b",
    }).json()["node"]
    c = client.post(f"/api/v1/workflows/{workflow_id}/instances/B/fork", json={
        "title": "C", "instanceId": "C", "initialMessage": "C local",
        "expectedContentRevision": 1, "idempotencyKey": "fork-c",
    }).json()["node"]
    e = client.post(f"/api/v1/workflows/{workflow_id}/instances/A/fork", json={
        "title": "E", "instanceId": "E", "initialMessage": "E sibling secret",
        "expectedContentRevision": 0, "idempotencyKey": "fork-e",
    }).json()["node"]
    return workflow_id, b, c, e


def test_artifacts_are_versioned_and_comparison_never_returns_transcripts():
    store = GraphStore(":memory:")
    with TestClient(create_app(store)) as client:
        workflow_id, _, _, _ = _workspace(client)
        first = client.post(f"/api/v1/workflows/{workflow_id}/artifacts", json={
            "name": "report", "kind": "report", "mimeType": "text/markdown",
            "content": "# v1", "instanceId": "C",
        }).json()
        second = client.post(f"/api/v1/workflows/{workflow_id}/artifacts", json={
            "name": "report", "kind": "report", "mimeType": "text/markdown",
            "content": "# v2", "instanceId": "C",
        }).json()
        assert (first["version"], second["version"]) == (1, 2)
        assert client.get(
            f"/api/v1/workflows/{workflow_id}/artifacts/{second['artifactId']}"
        ).json()["content"] == "# v2"

        comparison = client.post(f"/api/v1/workflows/{workflow_id}/comparisons", json={
            "instanceIds": ["C", "E"]
        }).json()
        assert comparison["transcriptsIncluded"] is False
        assert comparison["sharedRoute"] == [{"instanceId": "A", "title": "A"}]
        assert "E sibling secret" not in str(comparison)
        c_branch = next(item for item in comparison["branches"] if item["instanceId"] == "C")
        assert [item["title"] for item in c_branch["memoryRoute"]] == ["A", "B", "C"]
        assert [item["version"] for item in c_branch["artifacts"]] == [2, 1]
    store.close()


def test_accepted_knowledge_is_route_scoped_and_enters_agent_context_without_transcript_merge():
    store = GraphStore(":memory:")
    adapter = ScriptedMockAgentAdapter([ModelTurn(final_answer="used accepted fact")])
    with TestClient(create_app(store, agent_model=adapter)) as client:
        workflow_id, _, _, _ = _workspace(client)
        merged = client.post(f"/api/v1/workflows/{workflow_id}/knowledge-merges", json={
            "targetInstanceId": "C", "sourceInstanceIds": ["E"],
            "items": [{"sourceInstanceId": "E", "kind": "fact", "title": "Reviewed fact",
                       "content": "Only this approved conclusion crosses routes."}],
            "artifactIds": [],
        }).json()
        assert merged["transcriptsMerged"] is False
        assert client.get(
            f"/api/v1/workflows/{workflow_id}/instances/C/knowledge"
        ).json()["knowledgeItems"][0]["content"].startswith("Only this approved")
        assert client.get(
            f"/api/v1/workflows/{workflow_id}/instances/E/knowledge"
        ).json()["knowledgeItems"] == []

        revision = store.list_messages(workflow_id, "C", scope="local")["contentRevision"]
        run = client.post(f"/api/v1/workflows/{workflow_id}/instances/C/runs", json={
            "objective": "Use reviewed knowledge", "constraints": [], "deliverables": [],
            "acceptanceChecks": [], "expectedContentRevision": revision,
            "idempotencyKey": "knowledge-run",
        }).json()
        raw = store._conn.execute(
            "SELECT context_snapshot_json FROM agent_runs WHERE id=?", (run["runId"],)
        ).fetchone()[0]
        assert "Only this approved conclusion" in raw
        assert "E sibling secret" not in raw
    store.close()


def test_dataset_versions_and_experiment_freeze_dataset_routes_and_runs():
    store = GraphStore(":memory:")
    adapter = ScriptedMockAgentAdapter([ModelTurn(final_answer="score: 1")])
    with TestClient(create_app(store, agent_model=adapter)) as client:
        workflow_id, _, _, _ = _workspace(client)
        dataset = client.post(f"/api/v1/workflows/{workflow_id}/datasets", json={
            "name": "sentiment-mini", "description": "Two review cases",
            "cases": [
                {"id": "positive", "input": "great", "expected": "positive", "tags": ["smoke"]},
                {"id": "negative", "input": "bad", "expected": "negative", "tags": ["smoke"]},
            ],
        }).json()
        version_two = client.post(f"/api/v1/workflows/{workflow_id}/datasets", json={
            "name": "sentiment-mini", "cases": [{"id": "neutral", "input": "ok"}]
        }).json()
        assert dataset["version"] == 1 and version_two["version"] == 2

        revision = store.list_messages(workflow_id, "C", scope="local")["contentRevision"]
        run = client.post(f"/api/v1/workflows/{workflow_id}/instances/C/runs", json={
            "objective": "Evaluate", "constraints": [], "deliverables": [],
            "acceptanceChecks": [], "expectedContentRevision": revision,
            "idempotencyKey": "experiment-run",
        }).json()
        experiment = client.post(f"/api/v1/workflows/{workflow_id}/experiments", json={
            "name": "baseline", "datasetId": dataset["datasetId"], "instanceIds": ["C"],
            "runIds": [run["runId"]], "metric": "accuracy", "notes": "first pass",
        }).json()
        assert experiment["snapshot"]["dataset"]["version"] == 1
        assert experiment["snapshot"]["instances"][0]["route"] == ["A", "B", "C"]
        assert experiment["snapshot"]["runs"][0]["finalAnswer"] == "score: 1"
        assert client.get(f"/api/v1/workflows/{workflow_id}/experiments").json()[
            "experiments"
        ][0]["experimentId"] == experiment["experimentId"]
    store.close()


def test_regular_chat_receives_accepted_knowledge_without_sibling_transcript():
    store, llm = GraphStore(":memory:"), CapturingLLM()
    with TestClient(create_app(store, llm)) as client:
        workflow_id, _, _, _ = _workspace(client)
        client.post(f"/api/v1/workflows/{workflow_id}/knowledge-merges", json={
            "targetInstanceId": "C", "sourceInstanceIds": ["E"],
            "items": [{"sourceInstanceId": "E", "kind": "decision", "title": "Approved",
                       "content": "Use the reviewed lexicon."}], "artifactIds": [],
        })
        response = client.post(
            f"/api/v1/workflows/{workflow_id}/instances/C/chat", json={"content": "continue"}
        )
        assert response.status_code == 200
        serialized = str(llm.messages)
        assert "Use the reviewed lexicon" in serialized
        assert "E sibling secret" not in serialized
    store.close()
