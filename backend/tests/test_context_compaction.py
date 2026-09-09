from __future__ import annotations

from agent_runtime.compaction import COMPACTOR_VERSION, compact_route_messages
from agent_runtime.context import assemble_agent_context
from graph_core import GraphStore


def _request() -> dict:
    return {
        "objective": "Continue the selected route",
        "constraints": [],
        "deliverables": ["answer"],
        "acceptanceChecks": ["no sibling leakage"],
    }


def test_compaction_is_deterministic_auditable_and_keeps_recent_messages_complete():
    messages = [
        {"id": index, "role": "user" if index % 2 else "assistant",
         "content": f"old-{index}-" + ("x" * 900), "createdAt": f"volatile-{index}"}
        for index in range(1, 11)
    ]
    first = compact_route_messages(
        messages, target_instance_id="D", route_instance_ids=["A", "B", "C", "D"],
        route_revision_vector=[{"instanceId": "A", "contentRevision": 3}],
        budget_chars=4_000, recent_message_count=3,
    )
    second = compact_route_messages(
        [{**item, "createdAt": "changed metadata"} for item in messages],
        target_instance_id="D", route_instance_ids=["A", "B", "C", "D"],
        route_revision_vector=[{"instanceId": "A", "contentRevision": 3}],
        budget_chars=4_000, recent_message_count=3,
    )

    assert first.plan is not None
    assert first.plan["compactorVersion"] == COMPACTOR_VERSION
    assert first.plan["compactedMessages"] == 7
    assert first.plan["retainedMessages"] == 3
    assert first.messages[-3:] == messages[-3:]
    assert first.messages[0]["compacted"] is True
    assert "volatile" not in first.messages[0]["content"]
    assert first.plan == second.plan
    assert first.messages[0]["content"] == second.messages[0]["content"]


def test_route_compaction_never_writes_back_or_leaks_sibling_history():
    store = GraphStore(":memory:")
    try:
        graph = store.create_workflow(
            name="Routes", root_title="A", root_topic_id="A", root_instance_id="A"
        )
        workflow_id = graph["workflowId"]
        store.append_message(workflow_id, "A", role="user", content="A shared " + "a" * 700)
        store.fork(workflow_id, "A", title="B", topic_id="B", instance_id="B")
        store.append_message(workflow_id, "B", role="assistant", content="B shared " + "b" * 700)
        store.append_message(workflow_id, "B", role="user", content="B latest " + "l" * 700)
        store.fork(workflow_id, "B", title="C", topic_id="C", instance_id="C")
        store.append_message(workflow_id, "C", role="user", content="C private " + "c" * 700)
        store.fork(workflow_id, "C", title="D", topic_id="D", instance_id="D")
        store.append_message(workflow_id, "D", role="assistant", content="D private " + "d" * 700)
        store.fork(workflow_id, "B", title="E", topic_id="E", instance_id="E")
        store.append_message(workflow_id, "E", role="user", content="E private " + "e" * 700)

        d_preview = store.context_preview(workflow_id, "D", max_chars=100_000)
        e_preview = store.context_preview(workflow_id, "E", max_chars=100_000)
        d = compact_route_messages(
            d_preview["messages"], target_instance_id="D",
            route_instance_ids=[item["instanceId"] for item in d_preview["memoryRoute"]],
            route_revision_vector=d_preview["memoryRoute"], budget_chars=1_600,
            recent_message_count=1,
        )
        e = compact_route_messages(
            e_preview["messages"], target_instance_id="E",
            route_instance_ids=[item["instanceId"] for item in e_preview["memoryRoute"]],
            route_revision_vector=e_preview["memoryRoute"], budget_chars=1_600,
            recent_message_count=1,
        )

        assert d.plan and e.plan
        assert d.plan["routeInstanceIds"] == ["A", "B", "C", "D"]
        assert e.plan["routeInstanceIds"] == ["A", "B", "E"]
        assert "E private" not in str(d.messages)
        assert "C private" not in str(e.messages) and "D private" not in str(e.messages)
        assert "A shared" in str(d.messages) and "B latest" in str(d.messages)
        assert "A shared" in str(e.messages) and "B latest" in str(e.messages)
        assert d.plan["summarySha256"] != e.plan["summarySha256"]
        # Provider projection must not replace or shorten the canonical graph.
        assert store.list_messages(workflow_id, "A", scope="local")["messages"][0][
            "content"
        ].endswith("a" * 700)

        old_source_hash = e.plan["sourceMessageSetSha256"]
        store.append_message(workflow_id, "B", role="assistant", content="B newest parent update")
        updated = store.context_preview(workflow_id, "E", max_chars=100_000)
        updated_e = compact_route_messages(
            updated["messages"], target_instance_id="E",
            route_instance_ids=[item["instanceId"] for item in updated["memoryRoute"]],
            route_revision_vector=updated["memoryRoute"], budget_chars=1_600,
            recent_message_count=1,
        )
        assert updated_e.plan
        assert updated_e.plan["sourceMessageSetSha256"] != old_source_hash
        assert "B newest parent update" in str(updated_e.messages)
    finally:
        store.close()


def test_compaction_changes_only_route_projection_not_stable_policy_tool_prefix():
    tools = [{
        "name": "safe_calculator", "version": "1.0.0", "description": "Calculate",
        "schema": {"type": "object", "properties": {}}, "sideEffect": "none",
    }]
    long_route = [
        {"id": index, "role": "user" if index % 2 else "assistant",
         "content": f"message-{index}-" + "x" * 600}
        for index in range(12)
    ]
    compacted = assemble_agent_context(
        route_messages=long_route, accepted_knowledge=[], request=_request(), tools=tools,
        target_instance_id="D", route_instance_ids=["A", "B", "C", "D"],
        context_budget_chars=3_000,
    )
    uncompressed = assemble_agent_context(
        route_messages=long_route, accepted_knowledge=[], request=_request(), tools=tools,
        target_instance_id="D", route_instance_ids=["A", "B", "C", "D"],
        context_budget_chars=100_000,
    )

    assert compacted.compaction_plan is not None
    assert uncompressed.compaction_plan is None
    assert compacted.stable_prefix_sha256 == uncompressed.stable_prefix_sha256
    assert compacted.request_sha256 != uncompressed.request_sha256
    assert compacted.messages[0] == uncompressed.messages[0]
