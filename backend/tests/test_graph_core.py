from __future__ import annotations

import pytest

from graph_core import Conflict, GraphStore, Validation


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


def test_fork_inherits_parent_messages_added_after_creation(store: GraphStore):
    wf = create(store)
    store.append_message(wf, "A", role="user", content="before fork")
    store.fork(wf, "A", title="B", topic_id="B", instance_id="B")
    store.append_message(wf, "A", role="user", content="after fork")
    child = store.list_messages(wf, "B")["messages"]
    assert [item["content"] for item in child] == ["before fork", "after fork"]
    assert all(item["inherited"] is True for item in child)


def test_fork_initial_message_is_child_local_and_bumps_content_revision(store: GraphStore):
    wf = create(store)
    before = store.get_graph(wf)["eventRevision"]
    forked = store.fork(wf, "A", title="B", instance_id="B", initial_message="branch work")
    messages = store.list_messages(wf, "B")["messages"]
    assert forked["node"]["id"] == "B"
    assert forked["contentRevision"] == before + 1
    assert [(item["content"], item["inherited"]) for item in messages] == [("branch work", False)]


def test_turn_canvas_groups_local_messages_and_exposes_route_context_counts(store: GraphStore):
    wf = create(store)
    store.append_message(wf, "A", role="user", content="A inherited context")
    store.fork(wf, "A", title="B", instance_id="B")
    store.append_message(wf, "B", role="system", content="local preamble")
    first = store.append_message(wf, "B", role="user", content="B1")
    store.append_message(wf, "B", role="assistant", content="B1 answer")
    store.append_message(wf, "B", role="tool", content="B1 tool")
    second = store.append_message(wf, "B", role="user", content="B2")
    store.append_message(wf, "B", role="assistant", content="B2 answer")
    pending = store.append_message(wf, "B", role="user", content="B3 pending")
    store.fork(wf, "A", title="E", instance_id="E", initial_message="sibling secret")

    canvas = store.list_turns(wf, "B")

    assert canvas["scope"] == "local"
    assert canvas["memoryRoute"] == [
        {"instanceId": "A", "title": "A"},
        {"instanceId": "B", "title": "B"},
    ]
    assert canvas["inheritedMessageCount"] == 1
    assert [message["content"] for message in canvas["preamble"]] == ["local preamble"]
    assert [turn["id"] for turn in canvas["turns"]] == [
        str(first["id"]), str(second["id"]), str(pending["id"])
    ]
    assert [turn["sequence"] for turn in canvas["turns"]] == [1, 2, 3]
    assert [turn["anchorMessageId"] for turn in canvas["turns"]] == [
        first["id"], second["id"], pending["id"]
    ]
    assert [turn["userMessage"]["content"] for turn in canvas["turns"]] == [
        "B1", "B2", "B3 pending"
    ]
    assert [[message["content"] for message in turn["responses"]] for turn in canvas["turns"]] == [
        ["B1 answer", "B1 tool"],
        ["B2 answer"],
        [],
    ]
    assert [turn["status"] for turn in canvas["turns"]] == ["completed", "completed", "pending"]
    assert canvas["eventExtensions"] == []
    assert "A inherited context" not in str(canvas["turns"])
    assert "sibling secret" not in str(canvas)


def test_fork_from_local_user_turn_keeps_anchor_but_tracks_later_parent_messages(store: GraphStore):
    wf = create(store)
    store.append_message(wf, "A", role="user", content="A context")
    store.fork(wf, "A", title="B", instance_id="B")
    store.append_message(wf, "B", role="user", content="B1")
    store.append_message(wf, "B", role="assistant", content="B1 answer")
    second = store.append_message(wf, "B", role="user", content="B2")
    store.append_message(wf, "B", role="system", content="B2 system response")
    store.append_message(wf, "B", role="assistant", content="B2 answer")
    second_tool = store.append_message(wf, "B", role="tool", content="B2 tool")
    third = store.append_message(wf, "B", role="user", content="B3")
    latest = store.append_message(wf, "B", role="assistant", content="B3 answer")

    source_revision = store.list_messages(wf, "B", scope="local")["contentRevision"]
    forked = store.fork(
        wf, "B", title="C from B2", instance_id="C", anchor_message_id=second["id"],
        expected_content_revision=source_revision, idempotency_key="fork-b2",
    )

    assert [message["content"] for message in store.list_messages(wf, "C")["messages"]] == [
        "A context", "B1", "B1 answer", "B2", "B2 system response", "B2 answer", "B2 tool",
        "B3", "B3 answer"
    ]
    assert forked["checkpointAnchor"] == {
        "kind": "localUserTurn",
        "anchorMessageId": second["id"],
        "turnId": str(second["id"]),
        "includedThroughLocalMessageId": second_tool["id"],
        "nextExcludedLocalUserMessageId": third["id"],
        "cursorValue": str(second["id"]),
        "sourceInstanceId": "B",
        "sourceContentRevision": latest["contentRevision"],
    }
    store.append_message(wf, "B", role="user", content="B4 after fork")
    assert "B4 after fork" in [
        message["content"] for message in store.list_messages(wf, "C")["messages"]
    ]


def test_anchored_fork_keeps_siblings_isolated_and_rejects_invalid_anchors(store: GraphStore):
    wf = create(store)
    inherited = store.append_message(wf, "A", role="user", content="inherited A")
    store.fork(wf, "A", title="B", instance_id="B")
    b_user = store.append_message(wf, "B", role="user", content="B local")
    b_answer = store.append_message(wf, "B", role="assistant", content="B answer")
    store.fork(wf, "A", title="E", instance_id="E")
    e_user = store.append_message(wf, "E", role="user", content="E sibling secret")

    source_revision = store.list_messages(wf, "B", scope="local")["contentRevision"]
    store.fork(
        wf, "B", title="C", instance_id="C", anchor_message_id=b_user["id"],
        expected_content_revision=source_revision, idempotency_key="fork-b-local",
    )
    assert [message["content"] for message in store.list_messages(wf, "C")["messages"]] == [
        "inherited A", "B local", "B answer"
    ]

    for invalid_anchor in (inherited["id"], b_answer["id"], e_user["id"], 999_999):
        with pytest.raises(Validation, match="local user message"):
            store.fork(
                wf, "B", title=f"invalid {invalid_anchor}",
                instance_id=f"invalid-{invalid_anchor}", anchor_message_id=invalid_anchor,
                expected_content_revision=source_revision,
                idempotency_key=f"invalid-anchor-{invalid_anchor}",
            )


@pytest.mark.parametrize(
    ("expected_content_revision", "idempotency_key", "message"),
    [
        (None, "fork-key", "expectedContentRevision is required"),
        (1, None, "idempotencyKey is required"),
    ],
)
def test_anchored_fork_requires_revision_and_idempotency_key(
    store: GraphStore,
    expected_content_revision: int | None,
    idempotency_key: str | None,
    message: str,
):
    wf = create(store)
    anchor = store.append_message(wf, "A", role="user", content="anchor")

    with pytest.raises(Validation, match=message):
        store.fork(
            wf, "A", title="unsafe child", anchor_message_id=anchor["id"],
            expected_content_revision=expected_content_revision,
            idempotency_key=idempotency_key,
        )

    assert len(store.get_graph(wf)["nodes"]) == 1


def test_anchored_fork_rejects_stale_content_revision_without_creating_child(store: GraphStore):
    wf = create(store)
    anchor = store.append_message(wf, "A", role="user", content="anchor")
    accepted_revision = anchor["contentRevision"]
    store.append_message(wf, "A", role="assistant", content="concurrent answer")

    with pytest.raises(Conflict, match="stale content revision"):
        store.fork(
            wf, "A", title="stale child", instance_id="stale-child",
            anchor_message_id=anchor["id"], expected_content_revision=accepted_revision,
            idempotency_key="stale-anchor",
        )
    assert "stale-child" not in {node["id"] for node in store.get_graph(wf)["nodes"]}


def test_anchored_fork_is_idempotent_and_rejects_key_reuse_with_new_arguments(store: GraphStore):
    wf = create(store)
    anchor = store.append_message(wf, "A", role="user", content="anchor")
    revision = anchor["contentRevision"]
    arguments = {
        "title": "B",
        "instance_id": "B",
        "anchor_message_id": anchor["id"],
        "expected_content_revision": revision,
        "idempotency_key": "fork-turn-1",
    }

    first = store.fork(wf, "A", **arguments)
    replay = store.fork(wf, "A", **arguments)

    assert replay == first
    assert first["idempotencyKey"] == "fork-turn-1"
    assert [node["id"] for node in store.get_graph(wf)["nodes"]].count("B") == 1
    assert store._conn.execute(
        "SELECT COUNT(*) FROM commands WHERE workflow_id=? AND command_type='fork'",
        (wf,),
    ).fetchone()[0] == 1
    with pytest.raises(Conflict, match="different arguments"):
        store.fork(wf, "A", **{**arguments, "title": "different"})


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
        ("parent after fork", True),
        ("child B local", False),
    ]
    assert default == effective
    assert "parent after fork" in [item["content"] for item in effective]
    assert "sibling E local" not in [item["content"] for item in effective]


def test_edit_latest_local_user_updates_children_but_not_siblings(store: GraphStore):
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
        "root context", "edited question"
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
    assert store._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 6
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


def test_checkpoint_anchor_metadata_survives_database_reopen(tmp_path):
    database = tmp_path / "anchor-audit.db"
    persistent = GraphStore(database)
    wf = create(persistent)
    persistent.append_message(wf, "A", role="user", content="A context")
    persistent.fork(wf, "A", title="B", instance_id="B")
    anchor = persistent.append_message(wf, "B", role="user", content="B anchor")
    persistent.append_message(wf, "B", role="assistant", content="B answer")
    source_revision = persistent.list_messages(wf, "B", scope="local")["contentRevision"]
    persistent.fork(
        wf, "B", title="C", instance_id="C", anchor_message_id=anchor["id"],
        expected_content_revision=source_revision, idempotency_key="persist-anchor",
    )
    persistent.close()

    reopened = GraphStore(database)
    try:
        node = next(node for node in reopened.get_graph(wf)["nodes"] if node["id"] == "C")
        expected = {
            "kind": "localUserTurn",
            "cursorValue": str(anchor["id"]),
            "sourceInstanceId": "B",
            "sourceContentRevision": source_revision,
            "turnId": str(anchor["id"]),
            "anchorMessageId": anchor["id"],
        }
        assert node["checkpointAnchor"] == expected
        assert reopened.list_turns(wf, "C")["checkpointAnchor"] == expected
    finally:
        reopened.close()
