from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from graph_core.migrations import run_migrations


class GraphError(Exception):
    pass


class NotFound(GraphError):
    pass


class Conflict(GraphError):
    pass


class Validation(GraphError):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


class GraphStore:
    """SQLite graph repository. A child checkpoint freezes its parent's effective history."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _init_schema(self) -> None:
        with self._lock:
            run_migrations(self._conn)

    def _workflow(self, cx: sqlite3.Connection, workflow_id: str) -> sqlite3.Row:
        row = cx.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row:
            raise NotFound("workflow not found")
        return row

    def _instance(self, cx: sqlite3.Connection, workflow_id: str, instance_id: str, active=False) -> sqlite3.Row:
        row = cx.execute(
            "SELECT * FROM conversation_instances WHERE workflow_id=? AND id=?",
            (workflow_id, instance_id),
        ).fetchone()
        if not row:
            raise NotFound("conversation instance not found")
        if active and row["status"] != "active":
            raise Validation("conversation instance is pruned")
        return row

    def _ensure_topic(self, cx: sqlite3.Connection, workflow_id: str, topic_id: str, name: str) -> None:
        cx.execute(
            "INSERT OR IGNORE INTO topics(id,workflow_id,name,created_at) VALUES(?,?,?,?)",
            (topic_id, workflow_id, name, _now()),
        )
        row = cx.execute("SELECT 1 FROM topics WHERE workflow_id=? AND id=?", (workflow_id, topic_id)).fetchone()
        if not row:
            raise Validation("topic could not be registered")

    def list_workflows(self) -> dict[str, Any]:
        with self._lock:
            ids = [row[0] for row in self._conn.execute("SELECT id FROM workflows ORDER BY updated_at DESC")]
        return {"workflows": [self.get_graph(workflow_id) for workflow_id in ids]}

    def create_workflow(self, *, name: str, root_title: str, root_topic_id: str | None = None,
                        provider: str = "local", root_instance_id: str | None = None,
                        provider_conversation_id: str | None = None) -> dict[str, Any]:
        if not name.strip() or not root_title.strip():
            raise Validation("name and rootTitle are required")
        workflow_id, instance_id = _id("wf"), root_instance_id or _id("ci")
        topic_id, checkpoint_id, now = root_topic_id or _id("topic"), _id("cp"), _now()
        with self.tx() as cx:
            cx.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?)",
                       (workflow_id, name.strip(), instance_id, instance_id, 1, 0, now, now))
            self._ensure_topic(cx, workflow_id, topic_id, root_title.strip())
            cx.execute(
                "INSERT INTO checkpoints"
                "(id,workflow_id,source_instance_id,source_content_revision,messages_json,created_at,"
                "source_cursor_kind,source_cursor_value) VALUES(?,?,?,?,?,?,?,?)",
                (checkpoint_id, workflow_id, None, 0, "[]", now, None, None),
            )
            cx.execute("INSERT INTO conversation_instances "
                       "(id,workflow_id,topic_id,parent_id,checkpoint_id,title,status,provider,"
                       "provider_conversation_id,content_revision,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (instance_id, workflow_id, topic_id, None, checkpoint_id, root_title.strip(),
                        "active", provider, provider_conversation_id, 0, now, now))
        return self.get_graph(workflow_id)

    def _route_ids(self, cx: sqlite3.Connection, workflow_id: str, instance_id: str) -> list[str]:
        route: list[str] = []
        current: str | None = instance_id
        seen: set[str] = set()
        while current:
            if current in seen:
                raise Validation("parent cycle detected")
            seen.add(current)
            row = self._instance(cx, workflow_id, current)
            route.append(current)
            current = row["parent_id"]
        return list(reversed(route))

    def _node(self, cx: sqlite3.Connection, workflow_id: str, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "topicId": row["topic_id"], "parentId": row["parent_id"],
            "title": row["title"], "status": row["status"], "provider": row["provider"],
            "providerConversationId": row["provider_conversation_id"],
            "contentRevision": row["content_revision"],
            "memoryRoute": self._route_ids(cx, workflow_id, row["id"]),
            "checkpointAnchor": self._checkpoint_anchor(cx, row),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def _checkpoint_anchor(self, cx: sqlite3.Connection,
                           instance: sqlite3.Row) -> dict[str, Any] | None:
        checkpoint = cx.execute(
            "SELECT source_instance_id,source_content_revision,source_cursor_kind,source_cursor_value "
            "FROM checkpoints WHERE id=?",
            (instance["checkpoint_id"],),
        ).fetchone()
        if not checkpoint or not checkpoint["source_cursor_kind"]:
            return None
        value = checkpoint["source_cursor_value"]
        result: dict[str, Any] = {
            "kind": checkpoint["source_cursor_kind"],
            "cursorValue": value,
            "sourceInstanceId": checkpoint["source_instance_id"],
            "sourceContentRevision": checkpoint["source_content_revision"],
        }
        if result["kind"] == "localUserTurn" and value is not None:
            result["turnId"] = value
            try:
                result["anchorMessageId"] = int(value)
            except ValueError:
                result["anchorMessageId"] = value
        elif result["kind"] == "instanceHead":
            result["anchorMessageId"] = None
        return result

    def get_graph(self, workflow_id: str) -> dict[str, Any]:
        with self._lock:
            wf = self._workflow(self._conn, workflow_id)
            rows = self._conn.execute(
                "SELECT * FROM conversation_instances WHERE workflow_id=? ORDER BY created_at,id", (workflow_id,)
            ).fetchall()
            return {"schemaVersion": 1, "workflowId": wf["id"], "name": wf["name"],
                    "rootInstanceId": wf["root_instance_id"], "activeInstanceId": wf["active_instance_id"],
                    "graphRevision": wf["graph_revision"], "eventRevision": wf["content_revision"],
                    "nodes": [self._node(self._conn, workflow_id, row) for row in rows]}

    def _local_messages(self, cx: sqlite3.Connection, instance_id: str) -> list[dict[str, Any]]:
        messages = [dict(row) for row in cx.execute(
            "SELECT id,role,content,created_at AS createdAt FROM local_messages WHERE instance_id=? ORDER BY id",
            (instance_id,),
        ).fetchall()]
        for message in messages:
            message["inherited"] = False
        return messages

    def _effective_messages(self, cx: sqlite3.Connection, workflow_id: str, instance_id: str) -> list[dict[str, Any]]:
        instance = self._instance(cx, workflow_id, instance_id)
        checkpoint = cx.execute("SELECT messages_json FROM checkpoints WHERE id=?", (instance["checkpoint_id"],)).fetchone()
        inherited = _loads(checkpoint[0], []) if checkpoint else []
        local = self._local_messages(cx, instance_id)
        for message in inherited:
            message["inherited"] = True
        return inherited + local

    def list_messages(self, workflow_id: str, instance_id: str,
                      scope: str = "effective") -> dict[str, Any]:
        if scope not in {"local", "effective"}:
            raise Validation("message scope must be local or effective")
        with self._lock:
            self._instance(self._conn, workflow_id, instance_id)
            wf = self._workflow(self._conn, workflow_id)
            instance = self._instance(self._conn, workflow_id, instance_id)
            messages = (self._local_messages(self._conn, instance_id) if scope == "local"
                        else self._effective_messages(self._conn, workflow_id, instance_id))
            return {"workflowId": workflow_id, "instanceId": instance_id,
                    "contentRevision": instance["content_revision"],
                    "eventRevision": wf["content_revision"],
                    "messages": messages}

    def list_turns(self, workflow_id: str, instance_id: str) -> dict[str, Any]:
        """Project one instance's local transcript into user-anchored turns.

        Turn Canvas is deliberately a local-only read model. Inherited
        checkpoint messages belong to the route context, not to this concrete
        conversation instance, and must not be repeated as cards here. A turn
        starts at a local user message and owns every following local message
        until the next local user message. ``eventExtensions`` is reserved for
        future non-message tool/error timeline entries.
        """
        with self._lock:
            instance = self._instance(self._conn, workflow_id, instance_id)
            wf = self._workflow(self._conn, workflow_id)
            local = self._local_messages(self._conn, instance_id)
            checkpoint = self._conn.execute(
                "SELECT messages_json FROM checkpoints WHERE id=?",
                (instance["checkpoint_id"],),
            ).fetchone()
            inherited_message_count = len(_loads(checkpoint["messages_json"], [])) if checkpoint else 0
            route_ids = self._route_ids(self._conn, workflow_id, instance_id)
            memory_route = [
                {
                    "instanceId": route_id,
                    "title": self._instance(self._conn, workflow_id, route_id)["title"],
                }
                for route_id in route_ids
            ]
            checkpoint_anchor = self._checkpoint_anchor(self._conn, instance)

        preamble: list[dict[str, Any]] = []
        turns: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for message in local:
            if message["role"] == "user":
                current = {
                    "id": str(message["id"]),
                    "sequence": len(turns) + 1,
                    "anchorMessageId": message["id"],
                    "userMessage": message,
                    "responses": [],
                    "status": "pending",
                }
                turns.append(current)
            elif current is None:
                preamble.append(message)
            else:
                current["responses"].append(message)
                if message["role"] == "assistant":
                    current["status"] = "completed"

        return {
            "workflowId": workflow_id,
            "instanceId": instance_id,
            "scope": "local",
            "memoryRoute": memory_route,
            "inheritedMessageCount": inherited_message_count,
            "contentRevision": instance["content_revision"],
            "eventRevision": wf["content_revision"],
            "checkpointAnchor": checkpoint_anchor,
            "preamble": preamble,
            "turns": turns,
            "eventExtensions": [],
        }

    def append_message(self, workflow_id: str, instance_id: str, *, role: str, content: str) -> dict[str, Any]:
        if role not in {"system", "user", "assistant", "tool"} or not content:
            raise Validation("invalid role or empty content")
        now = _now()
        with self.tx() as cx:
            self._instance(cx, workflow_id, instance_id, active=True)
            cur = cx.execute("INSERT INTO local_messages(workflow_id,instance_id,role,content,created_at) VALUES(?,?,?,?,?)",
                             (workflow_id, instance_id, role, content, now))
            cx.execute("UPDATE conversation_instances SET content_revision=content_revision+1,updated_at=? WHERE id=?",
                       (now, instance_id))
            cx.execute("UPDATE workflows SET content_revision=content_revision+1,updated_at=? WHERE id=?", (now, workflow_id))
            event_revision = self._workflow(cx, workflow_id)["content_revision"]
            revision = self._instance(cx, workflow_id, instance_id)["content_revision"]
        return {"id": cur.lastrowid, "instanceId": instance_id, "role": role, "content": content,
                "createdAt": now, "inherited": False, "contentRevision": revision,
                "eventRevision": event_revision}

    def _validate_latest_local_user_edit(self, cx: sqlite3.Connection, workflow_id: str,
                                         instance_id: str, message_id: int,
                                         expected_content_revision: int) -> sqlite3.Row:
        instance = self._instance(cx, workflow_id, instance_id, active=True)
        if instance["content_revision"] != expected_content_revision:
            raise Conflict("stale content revision")
        latest = cx.execute(
            "SELECT id,created_at FROM local_messages "
            "WHERE workflow_id=? AND instance_id=? AND role='user' ORDER BY id DESC LIMIT 1",
            (workflow_id, instance_id),
        ).fetchone()
        if not latest:
            raise NotFound("local user message not found")
        if latest["id"] != message_id:
            raise Conflict("message is not the latest local user message")
        return latest

    def prepare_latest_local_user_edit(self, workflow_id: str, instance_id: str,
                                       message_id: int, *, content: str,
                                       expected_content_revision: int) -> dict[str, Any]:
        if not content.strip():
            raise Validation("content must not be blank")
        with self._lock:
            self._validate_latest_local_user_edit(
                self._conn, workflow_id, instance_id, message_id, expected_content_revision
            )
            effective = self._effective_messages(self._conn, workflow_id, instance_id)
        virtual: list[dict[str, Any]] = []
        for message in effective:
            item = dict(message)
            if not item.get("inherited") and item["id"] == message_id:
                item["content"] = content
            if (not item.get("inherited") and item["id"] > message_id
                    and item["role"] in {"assistant", "tool"}):
                continue
            virtual.append(item)
        return {"messages": virtual, "contentRevision": expected_content_revision}

    def commit_latest_local_user_edit(self, workflow_id: str, instance_id: str,
                                      message_id: int, *, content: str,
                                      expected_content_revision: int,
                                      assistant_content: str | None = None) -> dict[str, Any]:
        if not content.strip():
            raise Validation("content must not be blank")
        if assistant_content is not None and not assistant_content.strip():
            raise Validation("assistant content must not be blank")
        now = _now()
        with self.tx() as cx:
            latest = self._validate_latest_local_user_edit(
                cx, workflow_id, instance_id, message_id, expected_content_revision
            )
            removed = [row["id"] for row in cx.execute(
                "SELECT id FROM local_messages WHERE workflow_id=? AND instance_id=? "
                "AND id>? AND role IN ('assistant','tool') ORDER BY id",
                (workflow_id, instance_id, message_id),
            ).fetchall()]
            cx.execute(
                "UPDATE local_messages SET content=? WHERE workflow_id=? AND instance_id=? AND id=?",
                (content, workflow_id, instance_id, message_id),
            )
            cx.execute(
                "DELETE FROM local_messages WHERE workflow_id=? AND instance_id=? "
                "AND id>? AND role IN ('assistant','tool')",
                (workflow_id, instance_id, message_id),
            )
            assistant_id: int | None = None
            if assistant_content is not None:
                assistant_id = cx.execute(
                    "INSERT INTO local_messages(workflow_id,instance_id,role,content,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (workflow_id, instance_id, "assistant", assistant_content.strip(), now),
                ).lastrowid
            cx.execute(
                "UPDATE conversation_instances SET content_revision=content_revision+1,updated_at=? WHERE id=?",
                (now, instance_id),
            )
            cx.execute(
                "UPDATE workflows SET content_revision=content_revision+1,updated_at=? WHERE id=?",
                (now, workflow_id),
            )
            revision = self._instance(cx, workflow_id, instance_id)["content_revision"]
            event_revision = self._workflow(cx, workflow_id)["content_revision"]
            local = self._local_messages(cx, instance_id)
        assistant_message = None
        if assistant_id is not None:
            assistant_message = {
                "id": assistant_id, "instanceId": instance_id, "role": "assistant",
                "content": assistant_content.strip(), "createdAt": now, "inherited": False,
                "contentRevision": revision, "eventRevision": event_revision,
            }
        return {
            "userMessage": {"id": message_id, "instanceId": instance_id, "role": "user",
                            "content": content, "createdAt": latest["created_at"], "inherited": False,
                            "contentRevision": revision, "eventRevision": event_revision},
            "removedMessageIds": removed,
            "regenerated": assistant_message is not None,
            "assistantMessage": assistant_message,
            "messages": local,
            "contentRevision": revision,
            "eventRevision": event_revision,
        }

    def _fork_checkpoint(self, cx: sqlite3.Connection, workflow_id: str,
                         parent_id: str, anchor_message_id: int | None
                         ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        effective = self._effective_messages(cx, workflow_id, parent_id)
        local = [message for message in effective if not message.get("inherited")]
        if anchor_message_id is None:
            return effective, {
                "kind": "instanceHead",
                "anchorMessageId": None,
                "includedThroughLocalMessageId": local[-1]["id"] if local else None,
            }

        anchor = cx.execute(
            "SELECT id FROM local_messages "
            "WHERE workflow_id=? AND instance_id=? AND id=? AND role='user'",
            (workflow_id, parent_id, anchor_message_id),
        ).fetchone()
        if not anchor:
            raise Validation(
                "anchorMessageId must reference a local user message in the source instance"
            )

        next_user = cx.execute(
            "SELECT id FROM local_messages "
            "WHERE workflow_id=? AND instance_id=? AND role='user' AND id>? "
            "ORDER BY id LIMIT 1",
            (workflow_id, parent_id, anchor_message_id),
        ).fetchone()
        next_user_id = next_user["id"] if next_user else None
        inherited = [message for message in effective if message.get("inherited")]
        included_local = [
            message for message in local
            if next_user_id is None or message["id"] < next_user_id
        ]
        return inherited + included_local, {
            "kind": "localUserTurn",
            "anchorMessageId": anchor_message_id,
            "turnId": str(anchor_message_id),
            "includedThroughLocalMessageId": included_local[-1]["id"],
            "nextExcludedLocalUserMessageId": next_user_id,
        }

    def fork(self, workflow_id: str, parent_id: str, *, title: str, topic_id: str | None = None,
             provider: str | None = None, instance_id: str | None = None,
             provider_conversation_id: str | None = None,
             initial_message: str | None = None,
             anchor_message_id: int | None = None,
             expected_content_revision: int | None = None,
             idempotency_key: str | None = None) -> dict[str, Any]:
        if not title.strip():
            raise Validation("title is required")
        if idempotency_key is not None and not idempotency_key.strip():
            raise Validation("idempotencyKey must not be blank")
        normalized_key = idempotency_key.strip() if idempotency_key is not None else None
        if anchor_message_id is not None:
            if expected_content_revision is None:
                raise Validation(
                    "expectedContentRevision is required when anchorMessageId is provided"
                )
            if normalized_key is None:
                raise Validation("idempotencyKey is required when anchorMessageId is provided")
        request = {
            "sourceInstanceId": parent_id,
            "title": title.strip(),
            "topicId": topic_id,
            "provider": provider,
            "instanceId": instance_id,
            "providerConversationId": provider_conversation_id,
            "initialMessage": initial_message.strip() if initial_message and initial_message.strip() else None,
            "anchorMessageId": anchor_message_id,
            "expectedContentRevision": expected_content_revision,
        }
        child_id, checkpoint_id, now = instance_id or _id("ci"), _id("cp"), _now()
        with self.tx() as cx:
            if normalized_key is not None:
                existing = cx.execute(
                    "SELECT request_json,response_json,status FROM commands "
                    "WHERE workflow_id=? AND idempotency_key=?",
                    (workflow_id, normalized_key),
                ).fetchone()
                if existing:
                    if _loads(existing["request_json"], {}) != request:
                        raise Conflict("idempotencyKey was already used with different arguments")
                    if existing["status"] == "completed":
                        return _loads(existing["response_json"], {})
                    raise Conflict("command is already in progress")
            parent = self._instance(cx, workflow_id, parent_id, active=True)
            if (expected_content_revision is not None
                    and parent["content_revision"] != expected_content_revision):
                raise Conflict(
                    "stale content revision: expected "
                    f"{expected_content_revision}, actual {parent['content_revision']}"
                )
            wf = self._workflow(cx, workflow_id)
            child_topic = topic_id or _id("topic")
            self._ensure_topic(cx, workflow_id, child_topic, title.strip())
            frozen, checkpoint_anchor = self._fork_checkpoint(
                cx, workflow_id, parent_id, anchor_message_id
            )
            command_id: str | None = None
            if normalized_key is not None:
                command_id = _id("cmd")
                cx.execute(
                    "INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?)",
                    (command_id, workflow_id, normalized_key, "fork", json.dumps(request),
                     None, "started", now, None),
                )
            cursor_value = (str(anchor_message_id) if anchor_message_id is not None
                            else str(parent["content_revision"]))
            checkpoint_anchor.update({
                "cursorValue": cursor_value,
                "sourceInstanceId": parent_id,
                "sourceContentRevision": parent["content_revision"],
            })
            cx.execute(
                "INSERT INTO checkpoints"
                "(id,workflow_id,source_instance_id,source_content_revision,messages_json,created_at,"
                "source_cursor_kind,source_cursor_value) VALUES(?,?,?,?,?,?,?,?)",
                (checkpoint_id, workflow_id, parent_id, parent["content_revision"],
                 json.dumps(frozen), now, checkpoint_anchor["kind"], cursor_value),
            )
            cx.execute("INSERT INTO conversation_instances "
                       "(id,workflow_id,topic_id,parent_id,checkpoint_id,title,status,provider,"
                       "provider_conversation_id,content_revision,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (child_id, workflow_id, child_topic, parent_id, checkpoint_id, title.strip(), "active",
                        provider or parent["provider"], provider_conversation_id, 0, now, now))
            if initial_message and initial_message.strip():
                cx.execute(
                    "INSERT INTO local_messages(workflow_id,instance_id,role,content,created_at) VALUES(?,?,?,?,?)",
                    (workflow_id, child_id, "user", initial_message.strip(), now),
                )
                cx.execute("UPDATE conversation_instances SET content_revision=content_revision+1 WHERE id=?", (child_id,))
                cx.execute("UPDATE workflows SET content_revision=content_revision+1 WHERE id=?", (workflow_id,))
            cx.execute("UPDATE workflows SET graph_revision=graph_revision+1,updated_at=? WHERE id=?", (now, workflow_id))
            node = self._node(cx, workflow_id, self._instance(cx, workflow_id, child_id))
            updated_wf = self._workflow(cx, workflow_id)
            child_revision = node["contentRevision"]
            response = {"node": node, "graphRevision": updated_wf["graph_revision"],
                        "contentRevision": child_revision,
                        "eventRevision": updated_wf["content_revision"],
                        "frozenParentContentRevision": parent["content_revision"],
                        "checkpointAnchor": checkpoint_anchor}
            if normalized_key is not None and command_id is not None:
                response["idempotencyKey"] = normalized_key
                cx.execute(
                    "UPDATE commands SET response_json=?,status='completed',completed_at=? WHERE id=?",
                    (json.dumps(response), now, command_id),
                )
            return response

    def activate(self, workflow_id: str, instance_id: str) -> dict[str, Any]:
        with self.tx() as cx:
            self._instance(cx, workflow_id, instance_id, active=True)
            cx.execute("UPDATE workflows SET active_instance_id=?,updated_at=? WHERE id=?", (instance_id, _now(), workflow_id))
            wf = self._workflow(cx, workflow_id)
        return {"workflowId": workflow_id, "activeInstanceId": instance_id,
                "graphRevision": wf["graph_revision"], "eventRevision": wf["content_revision"]}

    def topic_routes(self, workflow_id: str, topic_id: str, include_pruned: bool = False) -> dict[str, Any]:
        with self._lock:
            self._workflow(self._conn, workflow_id)
            sql = "SELECT * FROM conversation_instances WHERE workflow_id=? AND topic_id=?"
            args: list[Any] = [workflow_id, topic_id]
            if not include_pruned:
                sql += " AND status='active'"
            rows = self._conn.execute(sql + " ORDER BY created_at,id", args).fetchall()
            return {"workflowId": workflow_id, "topicId": topic_id,
                    "routes": [self._node(self._conn, workflow_id, row) for row in rows]}

    def _leaf_first(self, cx: sqlite3.Connection, workflow_id: str, target_id: str) -> list[str]:
        self._instance(cx, workflow_id, target_id, active=True)
        children: dict[str, list[str]] = {}
        for row in cx.execute("SELECT id,parent_id FROM conversation_instances WHERE workflow_id=? AND status='active'", (workflow_id,)):
            children.setdefault(row["parent_id"], []).append(row["id"])
        for values in children.values(): values.sort()
        result: list[str] = []
        def visit(node_id: str) -> None:
            for child in children.get(node_id, []): visit(child)
            result.append(node_id)
        visit(target_id)
        return result

    def prune_plan(self, workflow_id: str, target_id: str, *, allow_root: bool = False) -> dict[str, Any]:
        with self._lock:
            wf = self._workflow(self._conn, workflow_id)
            if target_id == wf["root_instance_id"] and not allow_root:
                raise Validation("pruning the root requires allowRoot")
            ids = self._leaf_first(self._conn, workflow_id, target_id)
            return {"workflowId": workflow_id, "targetInstanceId": target_id, "leafFirst": True,
                    "rootRemoval": target_id == wf["root_instance_id"], "graphRevision": wf["graph_revision"],
                    "nodes": [self._node(self._conn, workflow_id, self._instance(self._conn, workflow_id, item)) for item in ids]}

    def prune_commit(self, workflow_id: str, target_id: str, *, expected_revision: int,
                     idempotency_key: str, allow_root: bool = False) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise Validation("idempotencyKey is required")
        request = {"targetInstanceId": target_id, "expectedRevision": expected_revision, "allowRoot": allow_root}
        with self.tx() as cx:
            existing = cx.execute("SELECT request_json,response_json,status FROM commands WHERE workflow_id=? AND idempotency_key=?",
                                  (workflow_id, idempotency_key)).fetchone()
            if existing:
                if _loads(existing["request_json"], {}) != request:
                    raise Conflict("idempotencyKey was already used with different arguments")
                if existing["status"] == "completed":
                    return _loads(existing["response_json"], {})
                raise Conflict("command is already in progress")
            wf = self._workflow(cx, workflow_id)
            if wf["graph_revision"] != expected_revision:
                raise Conflict(f"stale graph revision: expected {expected_revision}, actual {wf['graph_revision']}")
            if target_id == wf["root_instance_id"] and not allow_root:
                raise Validation("pruning the root requires allowRoot")
            ids = self._leaf_first(cx, workflow_id, target_id)
            command_id, now = _id("cmd"), _now()
            cx.execute("INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?)",
                       (command_id, workflow_id, idempotency_key, "prune", json.dumps(request), None, "started", now, None))
            for item in ids:
                cx.execute("UPDATE conversation_instances SET status='pruned',updated_at=? WHERE id=?", (now, item))
                cx.execute("INSERT INTO tombstones VALUES(?,?,?,?)", (workflow_id, item, now, command_id))
            active = wf["active_instance_id"]
            if active in ids:
                parent = self._instance(cx, workflow_id, target_id)["parent_id"]
                active = parent
            cx.execute("UPDATE workflows SET active_instance_id=?,graph_revision=graph_revision+1,updated_at=? WHERE id=?",
                       (active, now, workflow_id))
            next_revision = self._workflow(cx, workflow_id)["graph_revision"]
            response = {"workflowId": workflow_id, "targetInstanceId": target_id,
                        "prunedInstanceIds": ids, "activeInstanceId": active,
                        "graphRevision": next_revision, "idempotencyKey": idempotency_key}
            cx.execute("UPDATE commands SET response_json=?,status='completed',completed_at=? WHERE id=?",
                       (json.dumps(response), now, command_id))
            return response
