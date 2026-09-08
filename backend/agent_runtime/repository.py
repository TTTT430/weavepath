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
from runtime_events import validate_event_type


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
        validate_event_type(event_type)
        sequence = cx.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM run_events WHERE run_id=?",
                              (run_id,)).fetchone()[0]
        cx.execute("INSERT INTO run_events VALUES(?,?,?,?,?)",
                   (run_id, sequence, event_type, _json(payload or {}), _now()))

    @staticmethod
    def _require_status(cx: sqlite3.Connection, run_id: str,
                        statuses: set[str]) -> sqlite3.Row:
        run = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise NotFound("agent run not found")
        if run["status"] not in statuses:
            raise Conflict("agent run is not active")
        return run

    @classmethod
    def _require_running(cls, cx: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        return cls._require_status(cx, run_id, {"running"})

    @staticmethod
    def _require_route_revision_vector(
        cx: sqlite3.Connection,
        workflow_id: str,
        instance_id: str,
        context: dict[str, Any],
    ) -> None:
        """Verify the exact live ancestor route captured for a run.

        New Runtime v2 runs persist ``A -> ... -> target`` as an ordered
        revision vector in their immutable context snapshot.  Checking the
        vector (rather than only the target revision) prevents a stale answer
        from committing after an inherited parent changes, while deliberately
        ignoring siblings that were never part of this run's model input.

        Contexts created by pre-vector Runtime versions are retained for
        recovery compatibility and continue to use the legacy target-only
        guard.
        """
        vector = context.get("routeRevisionVector")
        if vector is None:
            return
        if not isinstance(vector, list) or not vector:
            raise Conflict("run route revision vector is invalid")

        actual_reversed: list[sqlite3.Row] = []
        current_id: str | None = instance_id
        seen: set[str] = set()
        while current_id is not None:
            if current_id in seen:
                raise Conflict("run route revision vector is invalid")
            seen.add(current_id)
            row = cx.execute(
                "SELECT id,parent_id,content_revision,status "
                "FROM conversation_instances WHERE workflow_id=? AND id=?",
                (workflow_id, current_id),
            ).fetchone()
            if row is None or row["status"] != "active":
                raise Conflict("run route revision conflict")
            actual_reversed.append(row)
            current_id = row["parent_id"]
        actual = list(reversed(actual_reversed))

        if len(vector) != len(actual):
            raise Conflict("run route revision conflict")
        for expected, current in zip(vector, actual):
            if not isinstance(expected, dict):
                raise Conflict("run route revision vector is invalid")
            expected_id = expected.get("instanceId")
            expected_revision = expected.get("contentRevision")
            if (not isinstance(expected_id, str)
                    or not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)):
                raise Conflict("run route revision vector is invalid")
            if (expected_id != current["id"]
                    or expected_revision != current["content_revision"]):
                raise Conflict("run route revision conflict")

    def create(self, *, workflow_id: str, instance_id: str, request: dict[str, Any],
               context: dict[str, Any], model_snapshot: dict[str, Any],
               parent_run_id: str | None = None) -> tuple[dict[str, Any], bool]:
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
            self._require_route_revision_vector(
                cx, workflow_id, instance_id, context
            )
            run_id = "run_" + uuid.uuid4().hex
            root_run_id, attempt_number = run_id, 1
            if parent_run_id is not None:
                parent = cx.execute("SELECT * FROM agent_runs WHERE id=?", (parent_run_id,)).fetchone()
                if not parent:
                    raise NotFound("parent agent run not found")
                if (parent["workflow_id"] != workflow_id
                        or parent["instance_id"] != instance_id):
                    raise Conflict("retry parent belongs to a different route")
                root_run_id = parent["root_run_id"] or parent["id"]
                attempt_number = cx.execute(
                    "SELECT COALESCE(MAX(attempt_number),0)+1 FROM agent_runs WHERE root_run_id=?",
                    (root_run_id,),
                ).fetchone()[0]
            cx.execute(
                "INSERT INTO agent_runs(id,workflow_id,instance_id,status,input_content_revision,"
                "context_snapshot_json,context_sha256,model_snapshot_json,request_json,request_sha256,"
                "idempotency_key,objective,constraints_json,deliverables_json,acceptance_checks_json,"
                "final_message_id,error_code,created_at,updated_at,root_run_id,parent_run_id,attempt_number) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, workflow_id, instance_id, "queued", request["expectedContentRevision"],
                 _json(context), _hash(context), _json(model_snapshot), _json(request), request_sha,
                 request["idempotencyKey"], request["objective"], _json(request["constraints"]),
                 _json(request["deliverables"]), _json(request["acceptanceChecks"]), None, None, now, now,
                 root_run_id, parent_run_id, attempt_number),
            )
            self._event(cx, run_id, "run.created", {"inputContentRevision": request["expectedContentRevision"]})
            self._event(cx, run_id, "context.frozen", {"contextSha256": _hash(context)})
            if parent_run_id is not None:
                self._event(cx, run_id, "run.retry_created", {
                    "parentRunId": parent_run_id, "rootRunId": root_run_id,
                    "attemptNumber": attempt_number,
                })
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

    def record_model(self, run_id: str, sequence: int, turn_kind: str,
                     usage: dict[str, int | str | None] | None = None,
                     error_code: str | None = None) -> str:
        step_id, now = "step_" + uuid.uuid4().hex, _now()
        with self.tx() as cx:
            # A provider response may arrive after cooperative cancellation was
            # requested. It is still a real model call whose usage must be
            # journaled before the run converges to ``cancelled``.
            self._require_status(cx, run_id, {"running", "cancelling"})
            status = "failed" if error_code else "completed"
            cx.execute("INSERT INTO run_steps VALUES(?,?,?,?,?,?,?,?)",
                       (step_id, run_id, sequence, "model", status, 1, now, now))
            if usage is not None:
                safe_usage: dict[str, Any] = {
                    key: value for key, value in usage.items()
                    if key in {"inputTokens", "outputTokens", "cachedInputTokens",
                               "uncachedInputTokens"}
                    and (value is None or (isinstance(value, int) and not isinstance(value, bool)
                                           and value >= 0))
                }
                cache_status = usage.get("cacheStatus")
                if cache_status in {"reported", "not_reported", "unsupported", "invalid"}:
                    safe_usage["cacheStatus"] = cache_status
                elif (isinstance(usage.get("cachedInputTokens"), int)
                      or isinstance(usage.get("uncachedInputTokens"), int)):
                    safe_usage["cacheStatus"] = "reported"
                if safe_usage:
                    cx.execute(
                        "INSERT INTO model_step_usage(step_id,run_id,usage_json,created_at) VALUES(?,?,?,?)",
                        (step_id, run_id, _json(safe_usage), now),
                    )
            payload = {"stepSequence": sequence, "turn": turn_kind}
            if error_code:
                payload["errorCode"] = error_code
            self._event(
                cx, run_id, "model.failed" if error_code else "model.completed", payload
            )
        return step_id

    def record_tool(self, run_id: str, sequence: int, *, name: str, version: str,
                    arguments: dict[str, Any], output: dict[str, Any] | None,
                    error_code: str | None, duration_ms: int,
                    provider_call_id: str | None = None) -> tuple[str, str]:
        step_id, call_id, result_id, now = ("step_" + uuid.uuid4().hex,
            "call_" + uuid.uuid4().hex, "result_" + uuid.uuid4().hex, _now())
        status = "failed" if error_code else "completed"
        with self.tx() as cx:
            self._require_running(cx, run_id)
            cx.execute("INSERT INTO run_steps VALUES(?,?,?,?,?,?,?,?)",
                       (step_id, run_id, sequence, "tool", status, 1, now, now))
            cx.execute(
                "INSERT INTO tool_calls(id,run_id,step_id,tool_name,tool_version,arguments_json,"
                "status,created_at,completed_at,provider_call_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (call_id, run_id, step_id, name, version, _json(arguments), status, now, now,
                 provider_call_id),
            )
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
                "WHERE id=? AND status IN ('queued','running','awaiting_approval')",
                (code, _now(), run_id),
            ).rowcount
            if changed == 1:
                self._event(cx, run_id, "run.failed", {"errorCode": code})
                self._terminal.notify_all()

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        """Durably request cancellation and make pre-execution waits terminal."""
        now = _now()
        with self.tx() as cx:
            row = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NotFound("agent run not found")
            status = row["status"]
            if status in {"completed", "failed", "interrupted", "cancelled"}:
                return self.get(run_id)
            if status != "cancelling":
                changed = cx.execute(
                    "UPDATE agent_runs SET status='cancelling',updated_at=? WHERE id=? AND status=?",
                    (now, run_id, status),
                ).rowcount
                if changed != 1:
                    raise Conflict("agent run status changed while cancelling")
                self._event(cx, run_id, "run.cancel_requested")
            if status in {"queued", "awaiting_approval"}:
                pending = cx.execute(
                    "SELECT id,tool_call_id FROM run_approvals "
                    "WHERE run_id=? AND status='pending'", (run_id,),
                ).fetchall()
                for approval in pending:
                    cx.execute(
                        "UPDATE run_approvals SET status='rejected',decided_at=? "
                        "WHERE id=? AND status='pending'", (now, approval["id"]),
                    )
                    self._event(cx, run_id, "approval.rejected", {
                        "approvalId": approval["id"],
                        "toolCallId": approval["tool_call_id"],
                        "reason": "runCancelled",
                    })
                cx.execute(
                    "UPDATE tool_calls SET status='cancelled',completed_at=? "
                    "WHERE run_id=? AND status IN ('awaiting_approval','approved')",
                    (now, run_id),
                )
                cx.execute(
                    "UPDATE run_steps SET status='cancelled',completed_at=? "
                    "WHERE run_id=? AND kind='tool' "
                    "AND status IN ('awaiting_approval','approved')",
                    (now, run_id),
                )
                changed = cx.execute(
                    "UPDATE agent_runs SET status='cancelled',error_code='runCancelled',updated_at=? "
                    "WHERE id=? AND status='cancelling'", (now, run_id),
                ).rowcount
                if changed != 1:
                    raise Conflict("agent run status changed while cancelling")
                self._event(cx, run_id, "run.cancelled", {"errorCode": "runCancelled"})
            self._terminal.notify_all()
            row = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return self.get(row["id"])

    def finish_cancel(self, run_id: str) -> dict[str, Any]:
        with self.tx() as cx:
            row = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NotFound("agent run not found")
            if row["status"] == "cancelled":
                return self.get(run_id)
            if row["status"] not in {"running", "cancelling", "awaiting_approval", "queued"}:
                raise Conflict("agent run cannot be cancelled")
            now = _now()
            pending = cx.execute(
                "SELECT id,tool_call_id FROM run_approvals "
                "WHERE run_id=? AND status='pending'", (run_id,),
            ).fetchall()
            for approval in pending:
                cx.execute(
                    "UPDATE run_approvals SET status='rejected',decided_at=? "
                    "WHERE id=? AND status='pending'", (now, approval["id"]),
                )
                self._event(cx, run_id, "approval.rejected", {
                    "approvalId": approval["id"],
                    "toolCallId": approval["tool_call_id"],
                    "reason": "runCancelled",
                })
            cx.execute(
                "UPDATE tool_calls SET status='cancelled',completed_at=? "
                "WHERE run_id=? AND status IN ('awaiting_approval','approved')",
                (now, run_id),
            )
            cx.execute(
                "UPDATE run_steps SET status='cancelled',completed_at=? "
                "WHERE run_id=? AND kind='tool' "
                "AND status IN ('awaiting_approval','approved')",
                (now, run_id),
            )
            changed = cx.execute(
                "UPDATE agent_runs SET status='cancelled',error_code='runCancelled',updated_at=? "
                "WHERE id=? AND status=?", (now, run_id, row["status"]),
            ).rowcount
            if changed != 1:
                raise Conflict("agent run status changed while cancelling")
            self._event(cx, run_id, "run.cancelled", {"errorCode": "runCancelled"})
            self._terminal.notify_all()
        return self.get(run_id)

    def claim_approved_tool(self, run_id: str, approval_id: str) -> None:
        """Atomically linearize an approved side-effect call before execution.

        If cancellation wins the database race, the run is no longer
        ``running`` and no tool implementation may be invoked. If this claim
        wins, a later cancellation is cooperative and the call is journaled
        before the run reaches its terminal cancelled state.
        """
        with self.tx() as cx:
            self._require_running(cx, run_id)
            row = cx.execute(
                "SELECT ra.status AS approval_status,tc.* FROM run_approvals ra "
                "JOIN tool_calls tc ON tc.id=ra.tool_call_id "
                "WHERE ra.id=? AND ra.run_id=?", (approval_id, run_id),
            ).fetchone()
            if not row or row["approval_status"] != "approved":
                raise Conflict("tool call is not approved")
            changed = cx.execute(
                "UPDATE tool_calls SET status='executing' WHERE id=? AND status='approved'",
                (row["id"],),
            ).rowcount
            if changed != 1:
                raise Conflict("approved tool call was already claimed")
            step_changed = cx.execute(
                "UPDATE run_steps SET status='executing' WHERE id=? AND status='awaiting_approval'",
                (row["step_id"],),
            ).rowcount
            if step_changed != 1:
                raise Conflict("approved tool step could not be claimed")
            self._event(cx, run_id, "tool.started", {
                "toolCallId": row["id"], "toolName": row["tool_name"],
                "toolVersion": row["tool_version"], "approvalId": approval_id,
            })

    def status(self, run_id: str) -> str:
        with self.lock:
            row = self.conn.execute("SELECT status FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NotFound("agent run not found")
            return str(row["status"])

    def retry_payload(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute("SELECT request_json FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NotFound("agent run not found")
            return json.loads(row["request_json"])

    def request_approval(self, run_id: str, sequence: int, *, name: str, version: str,
                         arguments: dict[str, Any], side_effect: str,
                         provider_call_id: str | None = None) -> dict[str, Any]:
        """Persist a side-effect boundary before any tool implementation is invoked."""
        step_id, call_id, approval_id, now = (
            "step_" + uuid.uuid4().hex,
            "call_" + uuid.uuid4().hex,
            "approval_" + uuid.uuid4().hex,
            _now(),
        )
        with self.tx() as cx:
            self._require_running(cx, run_id)
            cx.execute("INSERT INTO run_steps VALUES(?,?,?,?,?,?,?,?)",
                       (step_id, run_id, sequence, "tool", "awaiting_approval", 1, now, None))
            cx.execute(
                "INSERT INTO tool_calls(id,run_id,step_id,tool_name,tool_version,arguments_json,"
                "status,created_at,completed_at,provider_call_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (call_id, run_id, step_id, name, version, _json(arguments), "awaiting_approval",
                 now, None, provider_call_id),
            )
            cx.execute(
                "INSERT INTO run_approvals(id,run_id,tool_call_id,side_effect,status,created_at,decided_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (approval_id, run_id, call_id, side_effect, "pending", now, None),
            )
            changed = cx.execute(
                "UPDATE agent_runs SET status='awaiting_approval',updated_at=? "
                "WHERE id=? AND status='running'", (now, run_id)
            ).rowcount
            if changed != 1:
                raise Conflict("agent run is no longer running")
            self._event(cx, run_id, "approval.required", {
                "approvalId": approval_id, "toolCallId": call_id, "toolName": name,
                "toolVersion": version, "sideEffect": side_effect,
            })
            self._terminal.notify_all()
        return self.get(run_id)

    def decide_approval(self, run_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision must be approved or rejected")
        now = _now()
        with self.tx() as cx:
            approval = cx.execute(
                "SELECT * FROM run_approvals WHERE id=? AND run_id=?", (approval_id, run_id)
            ).fetchone()
            if not approval:
                raise NotFound("approval request not found")
            if approval["status"] != "pending":
                if approval["status"] != decision:
                    raise Conflict("approval request was already decided differently")
                return self.get(run_id)
            run = self._require_status(cx, run_id, {"awaiting_approval"})
            changed = cx.execute(
                "UPDATE run_approvals SET status=?,decided_at=? WHERE id=? AND status='pending'",
                (decision, now, approval_id),
            ).rowcount
            if changed != 1:
                raise Conflict("approval request status changed")
            self._event(cx, run_id, f"approval.{decision}", {
                "approvalId": approval_id, "toolCallId": approval["tool_call_id"],
            })
            if decision == "approved":
                changed = cx.execute(
                    "UPDATE tool_calls SET status='approved' WHERE id=? AND status='awaiting_approval'",
                    (approval["tool_call_id"],),
                ).rowcount
                if changed != 1:
                    raise Conflict("tool call status changed")
                changed = cx.execute(
                    "UPDATE agent_runs SET status='running',updated_at=? "
                    "WHERE id=? AND status='awaiting_approval'", (now, run["id"]),
                ).rowcount
                if changed != 1:
                    raise Conflict("agent run status changed while approving")
                self._event(cx, run_id, "run.resumed", {"approvalId": approval_id})
            else:
                changed = cx.execute(
                    "UPDATE tool_calls SET status='cancelled',completed_at=? "
                    "WHERE id=? AND status='awaiting_approval'",
                    (now, approval["tool_call_id"]),
                ).rowcount
                if changed != 1:
                    raise Conflict("tool call status changed")
                changed = cx.execute(
                    "UPDATE run_steps SET status='cancelled',completed_at=? WHERE id=("
                    "SELECT step_id FROM tool_calls WHERE id=?) AND status='awaiting_approval'",
                    (now, approval["tool_call_id"]),
                ).rowcount
                if changed != 1:
                    raise Conflict("tool step status changed")
                changed = cx.execute(
                    "UPDATE agent_runs SET status='cancelled',error_code='approvalRejected',updated_at=? "
                    "WHERE id=? AND status='awaiting_approval'", (now, run_id),
                ).rowcount
                if changed != 1:
                    raise Conflict("agent run status changed while rejecting approval")
                self._event(cx, run_id, "run.cancelled", {"errorCode": "approvalRejected"})
            self._terminal.notify_all()
        return self.get(run_id)

    def wait_approval(self, run_id: str, approval_id: str) -> str:
        with self._terminal:
            while True:
                row = self.conn.execute(
                    "SELECT status FROM run_approvals WHERE id=? AND run_id=?",
                    (approval_id, run_id),
                ).fetchone()
                if not row:
                    raise NotFound("approval request not found")
                if row["status"] != "pending":
                    return str(row["status"])
                run = self.conn.execute("SELECT status FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                if not run:
                    raise NotFound("agent run not found")
                if run["status"] in {"cancelled", "failed", "interrupted"}:
                    return "rejected"
                self._terminal.wait(timeout=0.25)

    def finish_approved_tool(self, run_id: str, approval_id: str, *,
                             output: dict[str, Any] | None, error_code: str | None,
                             duration_ms: int) -> tuple[str, str]:
        """Finish the exact approved call; never creates a second side effect."""
        now, result_id = _now(), "result_" + uuid.uuid4().hex
        with self.tx() as cx:
            self._require_status(cx, run_id, {"running", "cancelling"})
            row = cx.execute(
                "SELECT ra.status AS approval_status,tc.*,rs.id AS run_step_id "
                "FROM run_approvals ra JOIN tool_calls tc ON tc.id=ra.tool_call_id "
                "JOIN run_steps rs ON rs.id=tc.step_id WHERE ra.id=? AND ra.run_id=?",
                (approval_id, run_id),
            ).fetchone()
            if not row or row["approval_status"] != "approved" or row["status"] != "executing":
                raise Conflict("tool call is not approved")
            status = "failed" if error_code else "completed"
            changed = cx.execute(
                "UPDATE tool_calls SET status=?,completed_at=? WHERE id=? AND status='executing'",
                (status, now, row["id"]),
            ).rowcount
            if changed != 1:
                raise Conflict("approved tool call was already consumed")
            cx.execute("UPDATE run_steps SET status=?,completed_at=? WHERE id=?",
                       (status, now, row["step_id"]))
            cx.execute("INSERT INTO tool_results VALUES(?,?,?,?,?,?,?)", (
                result_id, row["id"], _json(output) if output is not None else None,
                error_code, duration_ms, _hash(output) if output is not None else None, now,
            ))
            self._event(cx, run_id, "tool.failed" if error_code else "tool.completed", {
                "toolCallId": row["id"], "toolName": row["tool_name"],
                "toolVersion": row["tool_version"],
                **({"errorCode": error_code} if error_code else {}),
            })
        return row["step_id"], row["id"]

    def complete(self, run_id: str, answer: str) -> dict[str, Any]:
        now = _now()
        with self.tx() as cx:
            run = self._require_running(cx, run_id)
            context = json.loads(run["context_snapshot_json"])
            self._require_route_revision_vector(
                cx, run["workflow_id"], run["instance_id"], context
            )
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
            changed = cx.execute(
                "UPDATE agent_runs SET status='completed',final_message_id=?,final_answer=?,updated_at=? "
                "WHERE id=? AND status='running'",
                (message_id, answer, now, run_id),
            ).rowcount
            if changed != 1:
                raise Conflict("agent run status changed before completion")
            self._event(cx, run_id, "run.completed", {"finalMessageId": message_id})
            self._terminal.notify_all()
            row = cx.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        result = self._snapshot(row)
        result["finalAnswer"] = answer
        return result

    def recover_interrupted(self) -> int:
        count = 0
        with self.tx() as cx:
            now = _now()
            cancelling = cx.execute(
                "SELECT id FROM agent_runs WHERE status='cancelling'"
            ).fetchall()
            for row in cancelling:
                unfinished_calls = cx.execute(
                    "SELECT id,tool_name,tool_version,status FROM tool_calls "
                    "WHERE run_id=? AND status IN ('awaiting_approval','approved','executing')",
                    (row["id"],),
                ).fetchall()
                pending = cx.execute(
                    "SELECT id,tool_call_id FROM run_approvals "
                    "WHERE run_id=? AND status='pending'", (row["id"],),
                ).fetchall()
                for approval in pending:
                    cx.execute(
                        "UPDATE run_approvals SET status='rejected',decided_at=? "
                        "WHERE id=? AND status='pending'", (now, approval["id"]),
                    )
                    self._event(cx, row["id"], "approval.rejected", {
                        "approvalId": approval["id"],
                        "toolCallId": approval["tool_call_id"],
                        "reason": "runCancelledDuringRecovery",
                    })
                # An executing side effect may have crossed the process-crash
                # boundary, so journal it as interrupted rather than claiming
                # it definitely did or did not happen. Pre-execution calls are
                # safe to mark cancelled because this process will not resume
                # them automatically.
                cx.execute(
                    "UPDATE tool_calls SET status='cancelled',completed_at=? "
                    "WHERE run_id=? AND status IN ('awaiting_approval','approved')",
                    (now, row["id"]),
                )
                cx.execute(
                    "UPDATE tool_calls SET status='interrupted',completed_at=? "
                    "WHERE run_id=? AND status='executing'", (now, row["id"]),
                )
                cx.execute(
                    "UPDATE run_steps SET status='cancelled',completed_at=? "
                    "WHERE run_id=? AND kind='tool' "
                    "AND status IN ('awaiting_approval','approved')",
                    (now, row["id"]),
                )
                cx.execute(
                    "UPDATE run_steps SET status='interrupted',completed_at=? "
                    "WHERE run_id=? AND kind='tool' AND status='executing'",
                    (now, row["id"]),
                )
                for call in unfinished_calls:
                    executing = call["status"] == "executing"
                    self._event(
                        cx, row["id"],
                        "tool.interrupted" if executing else "tool.cancelled",
                        {
                            "toolCallId": call["id"],
                            "toolName": call["tool_name"],
                            "toolVersion": call["tool_version"],
                            "executionOutcome": "unknown" if executing else "notStarted",
                            "errorCode": "runInterrupted" if executing else "runCancelled",
                        },
                    )
                changed = cx.execute(
                    "UPDATE agent_runs SET status='cancelled',error_code='runCancelled',updated_at=? "
                    "WHERE id=? AND status='cancelling'", (now, row["id"]),
                ).rowcount
                if changed == 1:
                    self._event(cx, row["id"], "run.cancelled", {"errorCode": "runCancelled"})
                    count += 1
            rows = cx.execute(
                "SELECT id FROM agent_runs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                unfinished_calls = cx.execute(
                    "SELECT id,tool_name,tool_version,status FROM tool_calls "
                    "WHERE run_id=? AND status IN ('awaiting_approval','approved','executing')",
                    (row["id"],),
                ).fetchall()
                cx.execute(
                    "UPDATE tool_calls SET status='interrupted',completed_at=? "
                    "WHERE run_id=? AND status IN ('awaiting_approval','approved','executing')",
                    (now, row["id"]),
                )
                cx.execute(
                    "UPDATE run_steps SET status='interrupted',completed_at=? "
                    "WHERE run_id=? AND kind='tool' "
                    "AND status IN ('awaiting_approval','approved','executing')",
                    (now, row["id"]),
                )
                for call in unfinished_calls:
                    self._event(cx, row["id"], "tool.interrupted", {
                        "toolCallId": call["id"],
                        "toolName": call["tool_name"],
                        "toolVersion": call["tool_version"],
                        "executionOutcome": (
                            "unknown" if call["status"] == "executing" else "notStarted"
                        ),
                        "errorCode": "runInterrupted",
                    })
                changed = cx.execute(
                    "UPDATE agent_runs SET status='interrupted',error_code='runInterrupted',updated_at=? "
                    "WHERE id=? AND status IN ('queued','running')",
                    (now, row["id"]),
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
                if row["status"] in {"completed", "failed", "interrupted", "cancelled"}:
                    return self.get(run_id)
                self._terminal.wait(timeout=0.25)

    def get(self, run_id: str, details: bool = True) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NotFound("agent run not found")
            result = self._snapshot(row)
            result["approvalRequests"] = self._approvals(self.conn, run_id)
            result["artifacts"] = self._artifacts(self.conn, run_id)
            if details:
                result["steps"] = [self._step(x) for x in self.conn.execute(
                    "SELECT * FROM run_steps WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()]
                result["toolCalls"] = [self._call(x) for x in self.conn.execute(
                    "SELECT * FROM tool_calls WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()]
                result["toolResults"] = [self._result(x) for x in self.conn.execute(
                    "SELECT tr.* FROM tool_results tr JOIN tool_calls tc ON tc.id=tr.tool_call_id "
                    "WHERE tc.run_id=? ORDER BY tr.created_at,tr.id", (run_id,)).fetchall()]
                result["finalAnswer"] = row["final_answer"]
                result["metrics"] = self._metrics(row, result["steps"], result["toolResults"])
            return result

    def list(self, workflow_id: str, instance_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM agent_runs WHERE workflow_id=? AND instance_id=? ORDER BY created_at,id",
                                     (workflow_id, instance_id)).fetchall()
            return [dict(self._snapshot(row), approvalRequests=self._approvals(self.conn, row["id"]))
                    for row in rows]

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
                "routeRevisionVector": context.get("routeRevisionVector"),
                "acceptedKnowledge": context.get("acceptedKnowledge", []),
                "availableTools": context.get("availableTools", []),
                "promptLayoutVersion": context.get("promptLayoutVersion"),
                "stablePrefixSha256": context.get("stablePrefixSha256"),
                "modelRequestSha256": context.get("modelRequestSha256"),
                "objective": row["objective"], "constraints": json.loads(row["constraints_json"]),
                "deliverables": json.loads(row["deliverables_json"]),
                "acceptanceChecks": json.loads(row["acceptance_checks_json"]),
                "attemptNumber": row["attempt_number"], "rootRunId": row["root_run_id"] or row["id"],
                "parentRunId": row["parent_run_id"],
                "finalMessageId": row["final_message_id"], "errorCode": row["error_code"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def _metrics(self, row: sqlite3.Row, steps: list[dict[str, Any]],
                 tool_results: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
            duration_ms = max(0, int((updated - created).total_seconds() * 1000))
        except (TypeError, ValueError):
            duration_ms = None
        usage_rows = self.conn.execute(
            "SELECT usage_json FROM model_step_usage WHERE run_id=?", (row["id"],)
        ).fetchall()
        usage = [json.loads(item["usage_json"]) for item in usage_rows]
        def total(key: str, rows: list[dict[str, Any]] = usage) -> int | None:
            values = [item.get(key) for item in rows if isinstance(item.get(key), int)]
            return sum(values) if values else None
        input_tokens, output_tokens = total("inputTokens"), total("outputTokens")
        reported_usage = [item for item in usage if item.get("cacheStatus") == "reported"]
        cached_tokens = total("cachedInputTokens", reported_usage)
        uncached_tokens = total("uncachedInputTokens", reported_usage)
        # Build the ratio from complete per-call pairs. Never combine cached
        # tokens from one partial provider response with an input denominator
        # reported by another call; doing so can fabricate a ratio above 100%.
        ratio_cached = 0
        ratio_input = 0
        ratio_eligible_count = 0
        for item in reported_usage:
            cached = item.get("cachedInputTokens")
            input_count = item.get("inputTokens")
            uncached = item.get("uncachedInputTokens")
            if not isinstance(cached, int) or isinstance(cached, bool):
                continue
            if not isinstance(input_count, int) or isinstance(input_count, bool):
                if isinstance(uncached, int) and not isinstance(uncached, bool):
                    input_count = cached + uncached
                else:
                    continue
            if input_count < 0 or cached < 0 or cached > input_count:
                continue
            ratio_cached += cached
            ratio_input += input_count
            ratio_eligible_count += 1
        reuse_ratio = ratio_cached / ratio_input if ratio_input > 0 else None
        model_step_count = sum(step["kind"] == "model" for step in steps)
        coverage = ratio_eligible_count / model_step_count if model_step_count else None
        statuses = {item.get("cacheStatus") for item in usage}
        if "invalid" in statuses:
            cache_status = "invalid"
        elif "reported" in statuses:
            cache_status = "reported"
        elif "unsupported" in statuses:
            cache_status = "unsupported"
        else:
            cache_status = "not_reported"
        return {
            "durationMs": duration_ms,
            "modelStepCount": model_step_count,
            "toolCallCount": sum(step["kind"] == "tool" for step in steps),
            "toolDurationMs": sum(result["durationMs"] for result in tool_results),
            "inputTokens": input_tokens, "outputTokens": output_tokens,
            "cachedInputTokens": cached_tokens, "uncachedInputTokens": uncached_tokens,
            "cacheReuseRatio": reuse_ratio, "cacheCoverage": coverage,
            "cacheStatus": cache_status,
            "estimatedCost": None,
        }

    @staticmethod
    def _approvals(cx: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
        rows = cx.execute(
            "SELECT ra.*,tc.tool_name,tc.tool_version,tc.arguments_json "
            "FROM run_approvals ra JOIN tool_calls tc ON tc.id=ra.tool_call_id "
            "WHERE ra.run_id=? ORDER BY ra.created_at,ra.id", (run_id,)
        ).fetchall()
        return [{"approvalId": row["id"], "runId": row["run_id"],
                 "toolCallId": row["tool_call_id"], "toolName": row["tool_name"],
                 "toolVersion": row["tool_version"],
                 "arguments": json.loads(row["arguments_json"]),
                 "sideEffect": row["side_effect"], "status": row["status"],
                 "createdAt": row["created_at"], "decidedAt": row["decided_at"]}
                for row in rows]

    @staticmethod
    def _artifacts(cx: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
        rows = cx.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at,id", (run_id,)
        ).fetchall()
        return [{"artifactId": row["id"], "workflowId": row["workflow_id"],
                 "instanceId": row["instance_id"], "runId": row["run_id"],
                 "name": row["logical_name"], "version": row["version"],
                 "kind": row["kind"], "mimeType": row["mime_type"],
                 "metadata": json.loads(row["metadata_json"]), "sha256": row["sha256"],
                 "size": len(row["content_text"].encode()), "createdAt": row["created_at"]}
                for row in rows]

    @staticmethod
    def _step(row: sqlite3.Row) -> dict[str, Any]:
        return {"stepId": row["id"], "sequence": row["sequence"], "kind": row["kind"],
                "status": row["status"], "attempt": row["attempt"], "createdAt": row["created_at"],
                "completedAt": row["completed_at"]}

    @staticmethod
    def _call(row: sqlite3.Row) -> dict[str, Any]:
        return {"toolCallId": row["id"], "stepId": row["step_id"], "toolName": row["tool_name"],
                "toolVersion": row["tool_version"], "arguments": json.loads(row["arguments_json"]),
                "status": row["status"], "providerCallId": row["provider_call_id"]}

    @staticmethod
    def _result(row: sqlite3.Row) -> dict[str, Any]:
        return {"toolResultId": row["id"], "toolCallId": row["tool_call_id"],
                "output": json.loads(row["output_json"]) if row["output_json"] else None,
                "errorCode": row["error_code"], "durationMs": row["duration_ms"],
                "outputSha256": row["output_sha256"]}
