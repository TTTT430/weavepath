from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from graph_core import Conflict, NotFound


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class AgentRunRepository:
    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self.conn, self.lock = connection, lock
        self._terminal = threading.Condition(lock)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _event(self, cx: sqlite3.Connection, run_id: str, event_type: str,
               payload: dict[str, Any] | None = None) -> None:
        sequence = cx.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM run_events WHERE run_id=?",
                              (run_id,)).fetchone()[0]
        cx.execute("INSERT INTO run_events VALUES(?,?,?,?,?)",
                   (run_id, sequence, event_type, _json(payload or {}), _now()))

    @staticmethod
    def _require_running(cx: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        run = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise NotFound("agent run not found")
        if run["status"] != "running":
            raise Conflict("agent run is not running")
        return run

    def create(self, *, workflow_id: str, instance_id: str, request: dict[str, Any],
               context: dict[str, Any], model_snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        request_sha, now = _hash(request), _now()
        with self.tx() as cx:
            existing = cx.execute(
                "SELECT * FROM agent_runs WHERE workflow_id=? AND instance_id=? AND idempotency_key=?",
                (workflow_id, instance_id, request["idempotencyKey"]),
            ).fetchone()
            if existing:
                if existing["request_sha256"] != request_sha:
                    raise Conflict("idempotency key was already used with a different request")
                return self._snapshot(existing), False
            instance = cx.execute(
                "SELECT content_revision,status FROM conversation_instances WHERE workflow_id=? AND id=?",
                (workflow_id, instance_id),
            ).fetchone()
            if not instance:
                raise NotFound("conversation instance not found")
            if instance["status"] != "active":
                raise Conflict("conversation instance is not active")
            if instance["content_revision"] != request["expectedContentRevision"]:
                raise Conflict("stale content revision")
            run_id = "run_" + uuid.uuid4().hex
            cx.execute(
                "INSERT INTO agent_runs(id,workflow_id,instance_id,status,input_content_revision,"
                "context_snapshot_json,context_sha256,model_snapshot_json,request_json,request_sha256,"
                "idempotency_key,objective,constraints_json,deliverables_json,acceptance_checks_json,"
                "final_message_id,error_code,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, workflow_id, instance_id, "queued", request["expectedContentRevision"],
                 _json(context), _hash(context), _json(model_snapshot), _json(request), request_sha,
                 request["idempotencyKey"], request["objective"], _json(request["constraints"]),
                 _json(request["deliverables"]), _json(request["acceptanceChecks"]), None, None, now, now),
            )
            self._event(cx, run_id, "run.created", {"inputContentRevision": request["expectedContentRevision"]})
            self._event(cx, run_id, "context.frozen", {"contextSha256": _hash(context)})
            row = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return self._snapshot(row), True

    def find_idempotent(self, workflow_id: str, instance_id: str, request: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM agent_runs WHERE workflow_id=? AND instance_id=? AND idempotency_key=?",
                (workflow_id, instance_id, request["idempotencyKey"]),
            ).fetchone()
        if not row:
            return None
        if row["request_sha256"] != _hash(request):
            raise Conflict("idempotency key was already used with a different request")
        return self.get(row["id"])

    def start(self, run_id: str) -> None:
        with self.tx() as cx:
            changed = cx.execute(
                "UPDATE agent_runs SET status='running',updated_at=? WHERE id=? AND status='queued'",
                (_now(), run_id),
            ).rowcount
            if changed != 1:
                raise Conflict("agent run cannot be started")
            self._event(cx, run_id, "run.started")

    def event(self, run_id: str, event_type: str,
              payload: dict[str, Any] | None = None) -> None:
        """Append one redacted runtime event using the run's monotonic sequence."""
        with self.tx() as cx:
            self._require_running(cx, run_id)
            self._event(cx, run_id, event_type, payload)

    def record_model(self, run_id: str, sequence: int, turn_kind: str) -> str:
        step_id, now = "step_" + uuid.uuid4().hex, _now()
        with self.tx() as cx:
            self._require_running(cx, run_id)
            cx.execute("INSERT INTO run_steps VALUES(?,?,?,?,?,?,?,?)",
                       (step_id, run_id, sequence, "model", "completed", 1, now, now))
            self._event(cx, run_id, "model.completed", {"stepSequence": sequence, "turn": turn_kind})
        return step_id

    def record_tool(self, run_id: str, sequence: int, *, name: str, version: str,
                    arguments: dict[str, Any], output: dict[str, Any] | None,
                    error_code: str | None, duration_ms: int) -> tuple[str, str]:
        step_id, call_id, result_id, now = ("step_" + uuid.uuid4().hex,
            "call_" + uuid.uuid4().hex, "result_" + uuid.uuid4().hex, _now())
        status = "failed" if error_code else "completed"
        with self.tx() as cx:
            self._require_running(cx, run_id)
            cx.execute("INSERT INTO run_steps VALUES(?,?,?,?,?,?,?,?)",
                       (step_id, run_id, sequence, "tool", status, 1, now, now))
            cx.execute("INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?,?,?)",
                       (call_id, run_id, step_id, name, version, _json(arguments), status, now, now))
            output_json = _json(output) if output is not None else None
            cx.execute("INSERT INTO tool_results VALUES(?,?,?,?,?,?,?)",
                       (result_id, call_id, output_json, error_code, duration_ms,
                        _hash(output) if output is not None else None, now))
            self._event(cx, run_id, "tool.failed" if error_code else "tool.completed",
                        {"toolCallId": call_id, "toolName": name, "toolVersion": version,
                         **({"errorCode": error_code} if error_code else {})})
        return step_id, call_id

    def fail(self, run_id: str, code: str) -> None:
        with self.tx() as cx:
            changed = cx.execute(
                "UPDATE agent_runs SET status='failed',error_code=?,updated_at=? "
                "WHERE id=? AND status IN ('queued','running')",
                (code, _now(), run_id),
            ).rowcount
            if changed == 1:
                self._event(cx, run_id, "run.failed", {"errorCode": code})
                self._terminal.notify_all()

    def complete(self, run_id: str, answer: str) -> dict[str, Any]:
        now = _now()
        with self.tx() as cx:
            run = self._require_running(cx, run_id)
            instance = cx.execute("SELECT content_revision,status FROM conversation_instances WHERE id=? AND workflow_id=?",
                                  (run["instance_id"], run["workflow_id"])).fetchone()
            if not instance or instance["status"] != "active" or instance["content_revision"] != run["input_content_revision"]:
                raise Conflict("run revision conflict")
            message_id = cx.execute(
                "INSERT INTO local_messages(workflow_id,instance_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (run["workflow_id"], run["instance_id"], "assistant", answer, now),
            ).lastrowid
            cx.execute("UPDATE conversation_instances SET content_revision=content_revision+1,updated_at=? WHERE id=?",
                       (now, run["instance_id"]))
            cx.execute("UPDATE workflows SET content_revision=content_revision+1,updated_at=? WHERE id=?",
                       (now, run["workflow_id"]))
            cx.execute(
                "UPDATE agent_runs SET status='completed',final_message_id=?,final_answer=?,updated_at=? "
                "WHERE id=?",
                (message_id, answer, now, run_id),
            )
            self._event(cx, run_id, "run.completed", {"finalMessageId": message_id})
            self._terminal.notify_all()
            row = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        result = self._snapshot(row)
        result["finalAnswer"] = answer
        return result

    def recover_interrupted(self) -> int:
        count = 0
        with self.tx() as cx:
            rows = cx.execute(
                "SELECT id FROM agent_runs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                changed = cx.execute(
                    "UPDATE agent_runs SET status='interrupted',error_code='runInterrupted',updated_at=? "
                    "WHERE id=? AND status IN ('queued','running')",
                    (_now(), row["id"]),
                ).rowcount
                if changed == 1:
                    self._event(cx, row["id"], "run.interrupted", {"errorCode": "runInterrupted"})
                    count += 1
            if count:
                self._terminal.notify_all()
        return count

    def wait_terminal(self, run_id: str) -> dict[str, Any]:
        """Wait for a replayed synchronous run to reach a durable terminal state.

        The short timeout also observes a terminal transition committed by a
        different process, while the condition makes the normal single-process
        path wake immediately.
        """
        with self._terminal:
            while True:
                row = self.conn.execute("SELECT status FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                if not row:
                    raise NotFound("agent run not found")
                if row["status"] in {"completed", "failed", "interrupted"}:
                    return self.get(run_id)
                self._terminal.wait(timeout=0.25)

    def get(self, run_id: str, details: bool = True) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NotFound("agent run not found")
            result = self._snapshot(row)
            if details:
                result["steps"] = [self._step(x) for x in self.conn.execute(
                    "SELECT * FROM run_steps WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()]
                result["toolCalls"] = [self._call(x) for x in self.conn.execute(
                    "SELECT * FROM tool_calls WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()]
                result["toolResults"] = [self._result(x) for x in self.conn.execute(
                    "SELECT tr.* FROM tool_results tr JOIN tool_calls tc ON tc.id=tr.tool_call_id "
                    "WHERE tc.run_id=? ORDER BY tr.created_at,tr.id", (run_id,)).fetchall()]
                result["finalAnswer"] = row["final_answer"]
            return result

    def list(self, workflow_id: str, instance_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM agent_runs WHERE workflow_id=? AND instance_id=? ORDER BY created_at,id",
                                     (workflow_id, instance_id)).fetchall()
        return [self._snapshot(row) for row in rows]

    def events(self, run_id: str, after: int, limit: int) -> dict[str, Any]:
        self.get(run_id, details=False)
        with self.lock:
            rows = self.conn.execute("SELECT * FROM run_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                                     (run_id, after, limit)).fetchall()
        events = [{"sequence": r["sequence"], "type": r["event_type"],
                   "payload": json.loads(r["payload_json"]), "createdAt": r["created_at"]} for r in rows]
        return {"runId": run_id, "events": events,
                "nextAfterSequence": events[-1]["sequence"] if events else after}

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
        context = json.loads(row["context_snapshot_json"])
        return {"runId": row["id"], "workflowId": row["workflow_id"], "instanceId": row["instance_id"],
                "status": row["status"], "inputContentRevision": row["input_content_revision"],
                "contextSha256": row["context_sha256"], "modelSnapshot": json.loads(row["model_snapshot_json"]),
                "memoryRoute": context.get("memoryRoute", []),
                "availableTools": context.get("availableTools", []),
                "objective": row["objective"], "constraints": json.loads(row["constraints_json"]),
                "deliverables": json.loads(row["deliverables_json"]),
                "acceptanceChecks": json.loads(row["acceptance_checks_json"]),
                "finalMessageId": row["final_message_id"], "errorCode": row["error_code"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    @staticmethod
    def _step(row: sqlite3.Row) -> dict[str, Any]:
        return {"stepId": row["id"], "sequence": row["sequence"], "kind": row["kind"],
                "status": row["status"], "attempt": row["attempt"], "createdAt": row["created_at"],
                "completedAt": row["completed_at"]}

    @staticmethod
    def _call(row: sqlite3.Row) -> dict[str, Any]:
        return {"toolCallId": row["id"], "stepId": row["step_id"], "toolName": row["tool_name"],
                "toolVersion": row["tool_version"], "arguments": json.loads(row["arguments_json"]),
                "status": row["status"]}

    @staticmethod
    def _result(row: sqlite3.Row) -> dict[str, Any]:
        return {"toolResultId": row["id"], "toolCallId": row["tool_call_id"],
                "output": json.loads(row["output_json"]) if row["output_json"] else None,
                "errorCode": row["error_code"], "durationMs": row["duration_ms"],
                "outputSha256": row["output_sha256"]}
