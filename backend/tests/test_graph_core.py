from __future__ import annotations

import pytest

from graph_core import Conflict, GraphStore


@pytest.fixture
def store():
    value = GraphStore(":memory:")
    yield value
    value.close()


def create(store: GraphStore):
    graph = store.create_workflow(name="Dataset", root_title="A", root_topic_id="A", root_instance_id="A")
    return graph["workflowId"]


def test_route_instances_are_isolated_and_same_topic_has_two_routes(store: GraphStore):
    wf = create(store)
    store.fork(wf, "A", title="B", topic_id="B", instance_id="B")
    store.fork(wf, "B", title="C", topic_id="C", instance_id="C")
    store.fork(wf, "A", title="E", topic_id="E", instance_id="E")
    store.fork(wf, "C", title="D via C", topic_id="D", instance_id="D1")
    store.fork(wf, "E", title="D via E", topic_id="D", instance_id="D2")
    routes = store.topic_routes(wf, "D")["routes"]
    assert {tuple(row["memoryRoute"]) for row in routes} == {("A", "B", "C", "D1"), ("A", "E", "D2")}
    assert {row["id"] for row in routes} == {"D1", "D2"}


def test_fork_freezes_parent_checkpoint(store: GraphStore):
    wf = create(store)
    store.append_message(wf, "A", role="user", content="before fork")
    store.fork(wf, "A", title="B", topic_id="B", instance_id="B")
    store.append_message(wf, "A", role="user", content="after fork")
    child = store.list_messages(wf, "B")["messages"]
    assert [item["content"] for item in child] == ["before fork"]
    assert child[0]["inherited"] is True


def test_fork_initial_message_is_child_local_and_bumps_content_revision(store: GraphStore):
    wf = create(store)
    before = store.get_graph(wf)["eventRevision"]
    forked = store.fork(wf, "A", title="B", instance_id="B", initial_message="branch work")
    messages = store.list_messages(wf, "B")["messages"]
    assert forked["node"]["id"] == "B"
    assert forked["contentRevision"] == before + 1
    assert [(item["content"], item["inherited"]) for item in messages] == [("branch work", False)]


def test_local_and_effective_message_scopes_keep_routes_isolated(store: GraphStore):
    wf = create(store)
    store.append_message(wf, "A", role="user", content="parent before fork")
    store.fork(wf, "A", title="B", instance_id="B", initial_message="child B local")
    store.fork(wf, "A", title="E", instance_id="E", initial_message="sibling E local")
    store.append_message(wf, "A", role="user", content="parent after fork")

    local = store.list_messages(wf, "B", scope="local")["messages"]
    effective = store.list_messages(wf, "B", scope="effective")["messages"]
    default = store.list_messages(wf, "B")["messages"]

    assert [(item["content"], item["inherited"]) for item in local] == [
        ("child B local", False)
    ]
    assert [(item["content"], item["inherited"]) for item in effective] == [
        ("parent before fork", True),
        ("child B local", False),
    ]
    assert default == effective
    assert "parent after fork" not in [item["content"] for item in effective]
    assert "sibling E local" not in [item["content"] for item in effective]


def test_edit_latest_local_user_preserves_frozen_children_and_siblings(store: GraphStore):
    wf = create(store)
    store.append_message(wf, "A", role="user", content="root context")
    store.fork(wf, "A", title="B", instance_id="B")
    question = store.append_message(wf, "B", role="user", content="old question")
    old_answer = store.append_message(wf, "B", role="assistant", content="old answer")
    old_tool = store.append_message(wf, "B", role="tool", content="old tool")
    store.fork(wf, "B", title="C", instance_id="C")
    store.fork(wf, "A", title="E", instance_id="E", initial_message="sibling work")

    prepared = store.prepare_latest_local_user_edit(
        wf, "B", question["id"], content="edited question",
        expected_content_revision=old_tool["contentRevision"],
    )
    assert [item["content"] for item in prepared["messages"]] == [
        "root context", "edited question"
    ]
    assert [item["content"] for item in store.list_messages(wf, "B")["messages"]] == [
        "root context", "old question", "old answer", "old tool"
    ]
    result = store.commit_latest_local_user_edit(
        wf, "B", question["id"], content="edited question",
        expected_content_revision=old_tool["contentRevision"],
    )
    assert result["removedMessageIds"] == [old_answer["id"], old_tool["id"]]
    assert [(item["content"], item["inherited"]) for item in
            store.list_messages(wf, "B", scope="local")["messages"]] == [
        ("edited question", False)
    ]
    assert [item["content"] for item in store.list_messages(wf, "C")["messages"]] == [
        "root context", "old question", "old answer", "old tool"
    ]
    assert [item["content"] for item in store.list_messages(wf, "E")["messages"]] == [
        "root context", "sibling work"
    ]

    with pytest.raises(Conflict, match="stale content revision"):
        store.commit_latest_local_user_edit(
            wf, "B", question["id"], content="stale edit",
            expected_content_revision=old_tool["contentRevision"],
        )


def test_edit_rejects_message_that_is_not_latest_local_user(store: GraphStore):
    wf = create(store)
    first = store.append_message(wf, "A", role="user", content="first")
    latest = store.append_message(wf, "A", role="user", content="latest")
    with pytest.raises(Conflict, match="not the latest local user"):
        store.commit_latest_local_user_edit(
            wf, "A", first["id"], content="invalid target",
            expected_content_revision=latest["contentRevision"],
        )


def test_prune_is_leaf_first_idempotent_and_rejects_stale_revision(store: GraphStore):
    wf = create(store)
    store.fork(wf, "A", title="B", instance_id="B")
    store.fork(wf, "B", title="C", instance_id="C")
    plan = store.prune_plan(wf, "B")
    assert [node["id"] for node in plan["nodes"]] == ["C", "B"]
    store.fork(wf, "A", title="E", instance_id="E")
    with pytest.raises(Conflict, match="stale graph revision"):
        store.prune_commit(wf, "B", expected_revision=plan["graphRevision"], idempotency_key="prune-1")
    fresh = store.prune_plan(wf, "B")
    result = store.prune_commit(wf, "B", expected_revision=fresh["graphRevision"], idempotency_key="prune-2")
    assert result["prunedInstanceIds"] == ["C", "B"]
    assert store.prune_commit(wf, "B", expected_revision=fresh["graphRevision"], idempotency_key="prune-2") == result


def test_schema_and_revisions(store: GraphStore):
    wf = create(store)
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] in {"memory", "wal"}
    tables = {row[0] for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"workflows", "topics", "conversation_instances", "checkpoints", "local_messages", "tombstones", "commands", "schema_migrations"} <= tables
    assert store._conn.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1
    before = store.get_graph(wf)
    store.append_message(wf, "A", role="user", content="hello")
    after_message = store.get_graph(wf)
    assert after_message["graphRevision"] == before["graphRevision"]
    assert after_message["eventRevision"] == before["eventRevision"] + 1
    assert after_message["nodes"][0]["contentRevision"] == 1
    store.activate(wf, "A")
    assert store.get_graph(wf)["graphRevision"] == before["graphRevision"]


def test_content_revision_is_instance_scoped(store: GraphStore):
    wf = create(store)
    store.fork(wf, "A", title="B", instance_id="B")
    store.append_message(wf, "A", role="user", content="root only")
    graph = store.get_graph(wf)
    revisions = {node["id"]: node["contentRevision"] for node in graph["nodes"]}
    assert revisions == {"A": 1, "B": 0}
    assert store.list_messages(wf, "B")["contentRevision"] == 0


def test_topic_ids_are_scoped_to_workflow(store: GraphStore):
    first = store.create_workflow(name="one", root_title="A", root_topic_id="shared")
    second = store.create_workflow(name="two", root_title="A", root_topic_id="shared")
    assert first["workflowId"] != second["workflowId"]


def test_file_database_enables_wal(tmp_path):
    persistent = GraphStore(tmp_path / "workflow.db")
    try:
        assert persistent._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        persistent.close()
