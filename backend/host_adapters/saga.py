from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from graph_core import Conflict, NotFound, Validation


TERMINAL_SAGA_STATUSES = frozenset({"completed", "compensated", "orphaned", "failed"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class HostOperationJournal:
    """Durable idempotency and compensation journal for cross-host mutations."""

    def __init__(self, conn: sqlite3.Connection, lock: RLock) -> None:
        self.conn = conn
        self.lock = lock

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "operationId": row["operation_id"],
            "workflowId": row["workflow_id"],
            "sourceInstanceId": row["source_instance_id"],
            "targetInstanceId": row["target_instance_id"],
            "operationType": row["operation_type"],
            "hostKind": row["host_kind"],
            "idempotencyKey": row["idempotency_key"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "hostResult": json.loads(row["host_result_json"]) if row["host_result_json"] else None,
            "localResult": json.loads(row["local_result_json"]) if row["local_result_json"] else None,
            "errorCode": row["error_code"],
            "errorMessage": row["error_message"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
        }

    def begin(self, *, workflow_id: str, source_instance_id: str | None,
              operation_type: str, host_kind: str, idempotency_key: str,
              request: dict[str, Any], target_instance_id: str | None = None) -> tuple[dict[str, Any], bool]:
        if operation_type not in {"fork", "navigate", "inspect", "archive", "rename"}:
            raise Validation("unsupported host operation type")
        if not idempotency_key.strip():
            raise Validation("idempotencyKey must not be blank")
        request_json = _stable_json(request)
        request_sha = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM host_operation_sagas WHERE workflow_id=? AND operation_type=? AND idempotency_key=?",
                (workflow_id, operation_type, idempotency_key),
            ).fetchone()
            if row is not None:
                if row["request_sha256"] != request_sha or row["request_json"] != request_json:
                    raise Conflict("host operation idempotencyKey was reused with different arguments")
                return self._public(row), False
            now = _now()
            operation_id = f"hop_{uuid.uuid4().hex}"
            self.conn.execute(
                "INSERT INTO host_operation_sagas(operation_id,workflow_id,source_instance_id,"
                "target_instance_id,operation_type,host_kind,idempotency_key,request_json,"
                "request_sha256,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (operation_id, workflow_id, source_instance_id, target_instance_id,
                 operation_type, host_kind, idempotency_key, request_json, request_sha,
                 "started", now, now),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM host_operation_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            assert row is not None
            return self._public(row), True

    def transition(self, operation_id: str, status: str, *,
                   host_result: dict[str, Any] | None = None,
                   local_result: dict[str, Any] | None = None,
                   target_instance_id: str | None = None,
                   error_code: str | None = None,
                   error_message: str | None = None) -> dict[str, Any]:
        if status not in {"host_succeeded", *TERMINAL_SAGA_STATUSES}:
            raise Validation("invalid host operation saga status")
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM host_operation_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise NotFound("host operation not found")
            if row["status"] in TERMINAL_SAGA_STATUSES:
                if row["status"] != status:
                    raise Conflict("host operation is already terminal")
                return self._public(row)
            now = _now()
            completed_at = now if status in TERMINAL_SAGA_STATUSES else None
            self.conn.execute(
                "UPDATE host_operation_sagas SET status=?,host_result_json=COALESCE(?,host_result_json),"
                "local_result_json=COALESCE(?,local_result_json),target_instance_id=COALESCE(?,target_instance_id),"
                "error_code=?,error_message=?,updated_at=?,completed_at=? WHERE operation_id=?",
                (status, _stable_json(host_result) if host_result is not None else None,
                 _stable_json(local_result) if local_result is not None else None,
                 target_instance_id, error_code, error_message, now, completed_at, operation_id),
            )
            self.conn.commit()
            updated = self.conn.execute(
                "SELECT * FROM host_operation_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            assert updated is not None
            return self._public(updated)

    def get(self, operation_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM host_operation_sagas WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise NotFound("host operation not found")
            return self._public(row)

    def list_incomplete(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM host_operation_sagas WHERE status IN ('started','host_succeeded') "
                "ORDER BY created_at,operation_id"
            ).fetchall()
            return [self._public(row) for row in rows]

    def recover_started(self) -> int:
        """Quarantine operations whose remote outcome became unknowable."""
        with self.lock:
            now = _now()
            changed = self.conn.execute(
                "UPDATE host_operation_sagas SET status='orphaned',"
                "error_code='hostOutcomeUnknown',"
                "error_message='Process stopped before the host result was durably recorded',"
                "updated_at=?,completed_at=? WHERE status='started'",
                (now, now),
            ).rowcount
            self.conn.commit()
            return changed


__all__ = ["HostOperationJournal", "TERMINAL_SAGA_STATUSES"]
