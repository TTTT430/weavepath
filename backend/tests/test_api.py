from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.app import create_app
from api.llm import DisabledLLM
from api.llm import LLMUnavailable
from graph_core import GraphStore


class FakeLLM:
    def __init__(self):
        self.messages = []
        self.calls = 0

    def status(self):
        return {"configured": True, "provider": "fake", "model": "test-model", "reason": None}

    def complete(self, messages):
        self.calls += 1
        self.messages = messages
        return "assistant answer"


class CallbackLLM(FakeLLM):
    def __init__(self, callback, *, error: Exception | None = None):
        super().__init__()
        self.callback = callback
        self.error = error

    def complete(self, messages):
        self.messages = messages
        self.callback()
        if self.error:
            raise self.error
        return "replacement answer"


def test_camel_case_api_round_trip():
    store = GraphStore(":memory:")
    with TestClient(create_app(store)) as client:
        health = client.get("/api/v1/health").json()
        assert health["ok"] is True
        assert health["schemaVersion"] == 7
        created = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootTopicId": "A", "rootInstanceId": "A"
        })
        assert created.status_code == 201
        graph = created.json()
        wf = graph["workflowId"]
        assert graph["nodes"][0]["memoryRoute"] == ["A"]
        assert graph["nodes"][0]["contentRevision"] == 0
        assert client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={"role": "user", "content": "one"}).status_code == 201
        forked = client.post(f"/api/v1/workflows/{wf}/instances/A/fork", json={
            "title": "D", "topicId": "D", "instanceId": "D1", "initialMessage": "branch"
        })
        assert forked.status_code == 201
        assert forked.json()["node"]["memoryRoute"] == ["A", "D1"]
        listed = client.get(f"/api/v1/workflows/{wf}/instances/D1/messages").json()
        assert [item["inherited"] for item in listed["messages"]] == [True, False]
        routes = client.get(f"/api/v1/workflows/{wf}/topics/D/routes").json()
        assert routes["routes"][0]["topicId"] == "D"
    store.close()


def test_workflow_names_can_be_omitted_and_are_generated_from_first_message():
    store = GraphStore(":memory:")
    with TestClient(create_app(store, DisabledLLM())) as client:
        created = client.post("/api/v1/workflows", json={}).json()
        wf = created["workflowId"]
        assert created["name"] == "新工作流"
        assert created["nodes"][0]["title"] == "新对话"
        root = created["rootInstanceId"]
        message = client.post(
            f"/api/v1/workflows/{wf}/instances/{root}/messages",
            json={"role": "user", "content": "设计多模态情感分析数据集"},
        )
        assert message.status_code == 201
        graph = client.get(f"/api/v1/workflows/{wf}/graph").json()
        assert graph["name"] == "设计多模态情感分析数据集"
        assert graph["nodes"][0]["title"] == "设计多模态情感分析数据集"
        # Subsequent messages do not keep renaming the generated route.
        client.post(
            f"/api/v1/workflows/{wf}/instances/{root}/messages",
            json={"role": "user", "content": "后续问题"},
        )
        assert client.get(f"/api/v1/workflows/{wf}/graph").json()["name"] == "设计多模态情感分析数据集"
    store.close()


def test_ai_status_and_route_aware_chat_round_trip():
    store = GraphStore(":memory:")
    llm = FakeLLM()
    with TestClient(create_app(store, llm)) as client:
        assert client.get("/api/v1/ai/status").json() == {
            "configured": True, "provider": "fake", "model": "test-model", "reason": None
        }
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootTopicId": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/chat", json={"content": "question"}
        )
        assert response.status_code == 200
        assert response.json()["assistantMessage"]["content"] == "assistant answer"
        assert [(item["role"], item["content"]) for item in llm.messages] == [("user", "question")]
        listed = client.get(f"/api/v1/workflows/{wf}/instances/A/messages").json()["messages"]
        assert [(item["role"], item["content"]) for item in listed] == [
            ("user", "question"), ("assistant", "assistant answer")
        ]
    store.close()


def test_chat_idempotency_is_durable_and_context_preview_has_provenance(tmp_path):
    path = tmp_path / "chat.db"
    store = GraphStore(path)
    llm = FakeLLM()
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={"name": "W", "rootTitle": "A", "rootInstanceId": "A"}).json()
        workflow_id = graph["workflowId"]
        first = client.post(f"/api/v1/workflows/{workflow_id}/instances/A/chat",
                            json={"content": "hello", "idempotencyKey": "same"})
        replay = client.post(f"/api/v1/workflows/{workflow_id}/instances/A/chat",
                             json={"content": "hello", "idempotencyKey": "same"})
        assert first.status_code == replay.status_code == 200
        assert replay.json() == first.json()
        assert llm.calls == 1
        preview = client.get(f"/api/v1/workflows/{workflow_id}/instances/A/context-preview").json()
        assert preview["memoryRoute"][0]["instanceId"] == "A"
        assert all(item["sourceInstanceId"] == "A" for item in preview["messages"])
    store.close()
    reopened = GraphStore(path)
    with TestClient(create_app(reopened, FakeLLM())) as client:
        replay = client.post(f"/api/v1/workflows/{workflow_id}/instances/A/chat",
                             json={"content": "hello", "idempotencyKey": "same"})
        assert replay.status_code == 200
    reopened.close()


def test_fork_chat_answers_from_an_exact_turn_and_is_idempotent_without_sibling_leakage():
    store, llm = GraphStore(":memory:"), FakeLLM()
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Canvas", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        anchor = client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "user", "content": "shared question"
        }).json()
        client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "assistant", "content": "shared answer"
        })
        store.fork(wf, "A", title="Sibling", instance_id="E",
                   initial_message="sibling-only secret", expected_content_revision=2,
                   idempotency_key="sibling-fork")
        body = {"title": "Canvas branch", "initialMessage": "branch question",
                "anchorMessageId": anchor["id"], "expectedContentRevision": 2,
                "idempotencyKey": "canvas-fork"}
        response = client.post(f"/api/v1/workflows/{wf}/instances/A/fork-chat", json=body)
        assert response.status_code == 201
        result = response.json()
        child_id = result["node"]["id"]
        assert result["replyStatus"] == "completed"
        local = client.get(
            f"/api/v1/workflows/{wf}/instances/{child_id}/messages?scope=local"
        ).json()["messages"]
        assert [(item["role"], item["content"]) for item in local] == [
            ("user", "branch question"), ("assistant", "assistant answer")
        ]
        assert "shared question" in str(llm.messages)
        assert "sibling-only secret" not in str(llm.messages)

        top_graph = client.get(f"/api/v1/workflows/{wf}/graph").json()
        assert {node["id"] for node in top_graph["nodes"]} == {"A", "E"}
        assert child_id not in {node["id"] for node in top_graph["nodes"]}
        turn_tree = client.get(
            f"/api/v1/workflows/{wf}/instances/A/turn-tree"
        ).json()
        child_turn = next(
            turn for turn in turn_tree["turns"] if turn["routeInstanceId"] == child_id
        )
        assert child_turn["parentTurnId"] == str(anchor["id"])
        assert child_turn["userMessage"]["content"] == "branch question"

        activated = client.post(
            f"/api/v1/workflows/{wf}/instances/{child_id}/activate", json={}
        )
        assert activated.status_code == 200
        active_graph = client.get(f"/api/v1/workflows/{wf}/graph").json()
        assert active_graph["activeInstanceId"] == "A"
        assert active_graph["activeRouteInstanceId"] == child_id

        replay = client.post(f"/api/v1/workflows/{wf}/instances/A/fork-chat", json=body)
        assert replay.json()["node"]["id"] == child_id
        assert replay.json()["replyStatus"] == "completed"
        assert llm.calls == 1
    store.close()


def test_empty_fork_chat_creates_visible_turn_route_and_can_be_renamed():
    store, llm = GraphStore(":memory:"), FakeLLM()
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Canvas", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        anchor = client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "user", "content": "branch from here"
        }).json()

        forked = client.post(f"/api/v1/workflows/{wf}/instances/A/fork-chat", json={
            "anchorMessageId": anchor["id"],
            "expectedContentRevision": anchor["contentRevision"],
            "idempotencyKey": "empty-api-turn-branch",
        })

        assert forked.status_code == 201
        payload = forked.json()
        child_id = payload["node"]["id"]
        assert payload["node"]["title"] == "新分支 1"
        assert payload["node"]["surfaceScope"] == "turn"
        assert payload["replyStatus"] == "recorded"
        assert payload["assistantMessage"] is None
        assert llm.calls == 0
        assert {node["id"] for node in client.get(
            f"/api/v1/workflows/{wf}/graph"
        ).json()["nodes"]} == {"A"}
        route_nodes = client.get(
            f"/api/v1/workflows/{wf}/instances/A/turn-tree"
        ).json()["routeNodes"]
        assert any(route["routeInstanceId"] == child_id for route in route_nodes)

        first_message = client.post(
            f"/api/v1/workflows/{wf}/instances/{child_id}/messages",
            json={"role": "user", "content": "Try a larger sentiment model"},
        )
        assert first_message.status_code == 201
        assert first_message.json()["graphRevision"] == payload["graphRevision"] + 1
        updated_route = next(
            route for route in client.get(
                f"/api/v1/workflows/{wf}/instances/A/turn-tree"
            ).json()["routeNodes"]
            if route["routeInstanceId"] == child_id
        )
        assert updated_route["title"] == "Try a larger sentiment model"

        renamed = client.patch(
            f"/api/v1/workflows/{wf}/instances/{child_id}",
            json={
                "title": "Alternative analysis",
                "expectedRevision": first_message.json()["graphRevision"],
            },
        )
        assert renamed.status_code == 200
        assert renamed.json()["node"]["title"] == "Alternative analysis"
        stale = client.patch(
            f"/api/v1/workflows/{wf}/instances/{child_id}",
            json={"title": "Stale name", "expectedRevision": payload["graphRevision"]},
        )
        assert stale.status_code == 409
        blank = client.patch(
            f"/api/v1/workflows/{wf}/instances/{child_id}",
            json={"title": "   ", "expectedRevision": renamed.json()["graphRevision"]},
        )
        assert blank.status_code == 422
    store.close()


def test_message_scope_api_and_chat_context_remain_effective():
    store = GraphStore(":memory:")
    llm = FakeLLM()
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "user", "content": "parent context"
        })
        client.post(f"/api/v1/workflows/{wf}/instances/A/fork", json={
            "title": "B", "instanceId": "B", "initialMessage": "child context"
        })

        local = client.get(
            f"/api/v1/workflows/{wf}/instances/B/messages?scope=local"
        ).json()["messages"]
        effective = client.get(
            f"/api/v1/workflows/{wf}/instances/B/messages?scope=effective"
        ).json()["messages"]
        assert [(item["content"], item["inherited"]) for item in local] == [
            ("child context", False)
        ]
        assert [(item["content"], item["inherited"]) for item in effective] == [
            ("parent context", True), ("child context", False)
        ]

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/B/chat", json={"content": "new question"}
        )
        assert response.status_code == 200
        assert [(item["role"], item["content"]) for item in llm.messages] == [
            ("user", "parent context"),
            ("user", "child context"),
            ("user", "new question"),
        ]
    store.close()


def test_turn_canvas_and_fork_from_turn_api_contract():
    store = GraphStore(":memory:")
    with TestClient(create_app(store)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "user", "content": "A context"
        })
        client.post(f"/api/v1/workflows/{wf}/instances/A/fork", json={
            "title": "B", "instanceId": "B"
        })
        first = client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "user", "content": "B1"
        }).json()
        client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "assistant", "content": "B1 answer"
        })
        second = client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "user", "content": "B2"
        }).json()
        second_answer = client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "assistant", "content": "B2 answer"
        }).json()
        third = client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "user", "content": "B3"
        }).json()
        client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "assistant", "content": "B3 answer"
        })

        canvas_response = client.get(f"/api/v1/workflows/{wf}/instances/B/turns")
        assert canvas_response.status_code == 200
        canvas = canvas_response.json()
        assert canvas["memoryRoute"] == [
            {"instanceId": "A", "title": "A"},
            {"instanceId": "B", "title": "B"},
        ]
        assert canvas["inheritedMessageCount"] == 1
        assert [turn["id"] for turn in canvas["turns"]] == [
            str(first["id"]), str(second["id"]), str(third["id"])
        ]
        assert canvas["turns"][1]["responses"][0]["content"] == "B2 answer"

        forked = client.post(f"/api/v1/workflows/{wf}/instances/B/fork", json={
            "title": "C from B2",
            "instanceId": "C",
            "anchorMessageId": second["id"],
            "expectedContentRevision": canvas["contentRevision"],
            "idempotencyKey": "api-fork-b2",
        })
        assert forked.status_code == 201
        assert forked.json()["checkpointAnchor"]["kind"] == "localUserTurn"
        replay = client.post(f"/api/v1/workflows/{wf}/instances/B/fork", json={
            "title": "C from B2",
            "instanceId": "C",
            "anchorMessageId": second["id"],
            "expectedContentRevision": canvas["contentRevision"],
            "idempotencyKey": "api-fork-b2",
        })
        assert replay.status_code == 201
        assert replay.json() == forked.json()
        child = client.get(f"/api/v1/workflows/{wf}/instances/C/messages").json()["messages"]
        assert [message["content"] for message in child] == [
            "A context", "B1", "B1 answer", "B2", "B2 answer", "B3", "B3 answer"
        ]
        child_node = next(
            node for node in client.get(f"/api/v1/workflows/{wf}/graph").json()["nodes"]
            if node["id"] == "C"
        )
        assert child_node["checkpointAnchor"]["anchorMessageId"] == second["id"]

        stale = client.post(f"/api/v1/workflows/{wf}/instances/B/fork", json={
            "title": "stale", "instanceId": "stale",
            "anchorMessageId": second["id"],
            "expectedContentRevision": canvas["contentRevision"] - 1,
            "idempotencyKey": "api-stale-b2",
        })
        assert stale.status_code == 409
        assert stale.json()["code"] == "conflict"

        invalid = client.post(f"/api/v1/workflows/{wf}/instances/B/fork", json={
            "title": "invalid", "instanceId": "invalid",
            "anchorMessageId": second_answer["id"],
            "expectedContentRevision": canvas["contentRevision"],
            "idempotencyKey": "api-invalid-anchor",
        })
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "validationError"

        missing_guard = client.post(f"/api/v1/workflows/{wf}/instances/B/fork", json={
            "title": "missing guard", "anchorMessageId": second["id"],
        })
        assert missing_guard.status_code == 422
        assert missing_guard.json()["code"] == "validationError"
    store.close()


def test_regenerate_latest_local_user_uses_effective_context():
    store = GraphStore(":memory:")
    llm = FakeLLM()
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "user", "content": "parent context"
        })
        client.post(f"/api/v1/workflows/{wf}/instances/A/fork", json={
            "title": "B", "instanceId": "B"
        })
        question = client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "user", "content": "old question"
        }).json()
        answer = client.post(f"/api/v1/workflows/{wf}/instances/B/messages", json={
            "role": "assistant", "content": "old answer"
        }).json()

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/B/messages/{question['id']}/regenerate",
            json={"content": "edited question", "expectedRevision": answer["contentRevision"]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["regenerated"] is True
        assert payload["removedMessageIds"] == [answer["id"]]
        assert [(item["role"], item["content"]) for item in payload["messages"]] == [
            ("user", "edited question"), ("assistant", "assistant answer")
        ]
        assert [(item["role"], item["content"]) for item in llm.messages] == [
            ("user", "parent context"), ("user", "edited question")
        ]
    store.close()


def test_regenerate_in_record_only_mode_only_saves_edit():
    store = GraphStore(":memory:")
    with TestClient(create_app(store, DisabledLLM())) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        question = client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "user", "content": "old question"
        }).json()
        answer = client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "assistant", "content": "recorded answer"
        }).json()
        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/messages/{question['id']}/regenerate",
            json={"content": "edited only", "expectedRevision": answer["contentRevision"]},
        )
        assert response.status_code == 200
        assert response.json()["regenerated"] is False
        assert response.json()["assistantMessage"] is None
        assert [(item["role"], item["content"]) for item in response.json()["messages"]] == [
            ("user", "edited only")
        ]

        stale = client.post(
            f"/api/v1/workflows/{wf}/instances/A/messages/{question['id']}/regenerate",
            json={"content": "stale", "expectedRevision": answer["contentRevision"]},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "conflict"
    store.close()


def test_regenerate_model_failure_leaves_database_unchanged():
    store = GraphStore(":memory:")
    llm = CallbackLLM(lambda: None, error=LLMUnavailable("AI provider is unavailable"))
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = graph["workflowId"]
        question = client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "user", "content": "old question"
        }).json()
        answer = client.post(f"/api/v1/workflows/{wf}/instances/A/messages", json={
            "role": "assistant", "content": "old answer"
        }).json()
        before = store.list_messages(wf, "A", scope="local")

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/messages/{question['id']}/regenerate",
            json={"content": "edited question", "expectedRevision": answer["contentRevision"]},
        )
        assert response.status_code == 503
        assert store.list_messages(wf, "A", scope="local") == before
    store.close()


def test_regenerate_rechecks_revision_after_model_call():
    store = GraphStore(":memory:")
    state: dict[str, str] = {}
    llm = CallbackLLM(lambda: store.append_message(
        state["wf"], "A", role="user", content="concurrent question"
    ))
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = state["wf"] = graph["workflowId"]
        question = store.append_message(wf, "A", role="user", content="old question")
        answer = store.append_message(wf, "A", role="assistant", content="old answer")

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/A/messages/{question['id']}/regenerate",
            json={"content": "edited question", "expectedRevision": answer["contentRevision"]},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "conflict"
        assert [item["content"] for item in store.list_messages(wf, "A", scope="local")["messages"]] == [
            "old question", "old answer", "concurrent question"
        ]
    store.close()


def test_fork_during_generation_keeps_audit_snapshot_but_tracks_parent_after_commit():
    store = GraphStore(":memory:")
    state: dict[str, str] = {}
    llm = CallbackLLM(lambda: store.fork(state["wf"], "B", title="C", instance_id="C"))
    with TestClient(create_app(store, llm)) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Workflow", "rootTitle": "A", "rootInstanceId": "A"
        }).json()
        wf = state["wf"] = graph["workflowId"]
        store.fork(wf, "A", title="B", instance_id="B")
        question = store.append_message(wf, "B", role="user", content="old question")
        answer = store.append_message(wf, "B", role="assistant", content="old answer")

        response = client.post(
            f"/api/v1/workflows/{wf}/instances/B/messages/{question['id']}/regenerate",
            json={"content": "edited question", "expectedRevision": answer["contentRevision"]},
        )
        assert response.status_code == 200
        assert [item["content"] for item in store.list_messages(wf, "B")["messages"]] == [
            "edited question", "replacement answer"
        ]
        assert [item["content"] for item in store.list_messages(wf, "C")["messages"]] == [
            "edited question", "replacement answer"
        ]
        checkpoint = store._conn.execute(
            "SELECT messages_json FROM checkpoints WHERE id=(SELECT checkpoint_id FROM conversation_instances WHERE id='C')"
        ).fetchone()
        assert [item["content"] for item in json.loads(checkpoint["messages_json"])] == [
            "old question", "old answer"
        ]
    store.close()
