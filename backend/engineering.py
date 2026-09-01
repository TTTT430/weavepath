from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from graph_core import NotFound, Validation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


class EngineeringRepository:
    """Durable engineering records that reference routes without copying transcripts."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self.conn, self.lock = connection, lock

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

    @staticmethod
    def _workflow(cx: sqlite3.Connection, workflow_id: str) -> sqlite3.Row:
        row = cx.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row:
            raise NotFound("workflow not found")
        return row

    @staticmethod
    def _instance(cx: sqlite3.Connection, workflow_id: str, instance_id: str,
                  active: bool = False) -> sqlite3.Row:
        row = cx.execute(
            "SELECT * FROM conversation_instances WHERE workflow_id=? AND id=?",
            (workflow_id, instance_id),
        ).fetchone()
        if not row:
            raise NotFound("conversation instance not found")
        if active and row["status"] != "active":
            raise Validation("conversation instance is pruned")
        return row

    def _route(self, cx: sqlite3.Connection, workflow_id: str, instance_id: str) -> list[sqlite3.Row]:
        current = self._instance(cx, workflow_id, instance_id)
        route, seen = [], set()
        while current:
            if current["id"] in seen:
                raise Validation("conversation route is invalid")
            seen.add(current["id"])
            route.append(current)
            current = (self._instance(cx, workflow_id, current["parent_id"])
                       if current["parent_id"] else None)
        route.reverse()
        return route

    @staticmethod
    def _artifact(row: sqlite3.Row, include_content: bool = False) -> dict[str, Any]:
        result = {
            "artifactId": row["id"], "workflowId": row["workflow_id"],
            "instanceId": row["instance_id"], "runId": row["run_id"],
            "name": row["logical_name"], "version": row["version"],
            "kind": row["kind"], "mimeType": row["mime_type"],
            "metadata": json.loads(row["metadata_json"]), "sha256": row["sha256"],
            "size": len(row["content_text"].encode()), "createdAt": row["created_at"],
        }
        if include_content:
            result["content"] = row["content_text"]
        return result

    def create_artifact(self, workflow_id: str, *, name: str, kind: str, mime_type: str,
                        content: str, instance_id: str | None = None,
                        run_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise Validation("artifact name is required")
        with self.tx() as cx:
            self._workflow(cx, workflow_id)
            if instance_id:
                self._instance(cx, workflow_id, instance_id)
            if run_id:
                run = cx.execute("SELECT workflow_id,instance_id,final_answer FROM agent_runs WHERE id=?",
                                 (run_id,)).fetchone()
                if not run or run["workflow_id"] != workflow_id:
                    raise Validation("artifact run does not belong to workflow")
                if instance_id and run["instance_id"] != instance_id:
                    raise Validation("artifact run does not belong to instance")
                instance_id = instance_id or run["instance_id"]
                if not content and run["final_answer"]:
                    content = run["final_answer"]
            version = cx.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM artifacts WHERE workflow_id=? AND logical_name=?",
                (workflow_id, name),
            ).fetchone()[0]
            artifact_id, created = _id("artifact"), _now()
            cx.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, workflow_id, instance_id, run_id, name, version, kind.strip(),
                 mime_type.strip(), content, _json(metadata or {}),
                 hashlib.sha256(content.encode()).hexdigest(), created),
            )
            row = cx.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return self._artifact(row, True)

    def list_artifacts(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.lock:
            self._workflow(self.conn, workflow_id)
            rows = self.conn.execute(
                "SELECT * FROM artifacts WHERE workflow_id=? ORDER BY created_at DESC,id DESC",
                (workflow_id,),
            ).fetchall()
        return [self._artifact(row) for row in rows]

    def get_artifact(self, workflow_id: str, artifact_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM artifacts WHERE workflow_id=? AND id=?", (workflow_id, artifact_id)
            ).fetchone()
        if not row:
            raise NotFound("artifact not found")
        return self._artifact(row, True)

    def compare(self, workflow_id: str, instance_ids: list[str]) -> dict[str, Any]:
        unique = list(dict.fromkeys(instance_ids))
        if len(unique) < 2 or len(unique) > 4:
            raise Validation("comparison requires two to four distinct instances")
        with self.lock:
            self._workflow(self.conn, workflow_id)
            routes = {item: self._route(self.conn, workflow_id, item) for item in unique}
            branches = []
            for instance_id in unique:
                instance = routes[instance_id][-1]
                latest = self.conn.execute(
                    "SELECT * FROM agent_runs WHERE workflow_id=? AND instance_id=? "
                    "ORDER BY created_at DESC,id DESC LIMIT 1",
                    (workflow_id, instance_id),
                ).fetchone()
                local = self.conn.execute(
                    "SELECT role,COUNT(*) count FROM local_messages WHERE workflow_id=? AND instance_id=? "
                    "GROUP BY role", (workflow_id, instance_id),
                ).fetchall()
                artifacts = self.conn.execute(
                    "SELECT * FROM artifacts WHERE workflow_id=? AND instance_id=? ORDER BY created_at DESC",
                    (workflow_id, instance_id),
                ).fetchall()
                run = None
                if latest:
                    run = {
                        "runId": latest["id"], "status": latest["status"],
                        "objective": latest["objective"],
                        "modelSnapshot": json.loads(latest["model_snapshot_json"]),
                        "finalAnswer": latest["final_answer"], "errorCode": latest["error_code"],
                        "createdAt": latest["created_at"],
                    }
                branches.append({
                    "instanceId": instance_id, "topicId": instance["topic_id"],
                    "title": instance["title"], "status": instance["status"],
                    "memoryRoute": [{"instanceId": row["id"], "title": row["title"]}
                                    for row in routes[instance_id]],
                    "localMessageCounts": {row["role"]: row["count"] for row in local},
                    "latestRun": run,
                    "artifacts": [self._artifact(row) for row in artifacts],
                })
            shared: list[dict[str, str]] = []
            for route_rows in zip(*(routes[item] for item in unique)):
                if len({row["id"] for row in route_rows}) != 1:
                    break
                shared.append({"instanceId": route_rows[0]["id"], "title": route_rows[0]["title"]})
        return {"workflowId": workflow_id, "instanceIds": unique,
                "sharedRoute": shared, "branches": branches,
                "transcriptsIncluded": False}

    def merge_knowledge(self, workflow_id: str, *, target_instance_id: str,
                        source_instance_ids: list[str], items: list[dict[str, Any]],
                        artifact_ids: list[str]) -> dict[str, Any]:
        sources = list(dict.fromkeys(source_instance_ids))
        if not sources or not items and not artifact_ids:
            raise Validation("merge requires selected knowledge or artifacts")
        now, merge_id = _now(), _id("merge")
        with self.tx() as cx:
            self._workflow(cx, workflow_id)
            self._instance(cx, workflow_id, target_instance_id, active=True)
            for source in sources:
                self._instance(cx, workflow_id, source)
            cx.execute("INSERT INTO knowledge_merges VALUES(?,?,?,?,?,?)",
                       (merge_id, workflow_id, target_instance_id, _json(sources), "accepted", now))
            created = []
            for item in items:
                source = item["sourceInstanceId"]
                if source not in sources:
                    raise Validation("knowledge source was not selected")
                source_run_id = item.get("sourceRunId")
                if source_run_id:
                    run = cx.execute("SELECT workflow_id,instance_id FROM agent_runs WHERE id=?",
                                     (source_run_id,)).fetchone()
                    if not run or run["workflow_id"] != workflow_id or run["instance_id"] != source:
                        raise Validation("knowledge run provenance is invalid")
                item_id = _id("knowledge")
                provenance = {"sourceInstanceId": source, "sourceRunId": source_run_id,
                              "acceptedByMergeId": merge_id}
                cx.execute("INSERT INTO knowledge_items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (item_id, merge_id, workflow_id, target_instance_id, source,
                            source_run_id, item["kind"], item["title"].strip(),
                            item["content"].strip(), _json(provenance), now))
                created.append({"knowledgeItemId": item_id, "kind": item["kind"],
                                "title": item["title"].strip(), "content": item["content"].strip(),
                                "provenance": provenance})
            linked = []
            for artifact_id in list(dict.fromkeys(artifact_ids)):
                artifact = cx.execute("SELECT * FROM artifacts WHERE workflow_id=? AND id=?",
                                      (workflow_id, artifact_id)).fetchone()
                if not artifact:
                    raise Validation("merge artifact was not found")
                if artifact["instance_id"] and artifact["instance_id"] not in sources:
                    raise Validation("merge artifact does not belong to a selected source")
                cx.execute("INSERT INTO knowledge_merge_artifacts VALUES(?,?)", (merge_id, artifact_id))
                linked.append(self._artifact(artifact))
        return {"mergeId": merge_id, "workflowId": workflow_id,
                "targetInstanceId": target_instance_id, "sourceInstanceIds": sources,
                "knowledgeItems": created, "artifacts": linked, "transcriptsMerged": False,
                "createdAt": now}

    def accepted_knowledge(self, workflow_id: str, instance_id: str) -> list[dict[str, Any]]:
        with self.lock:
            route_ids = [row["id"] for row in self._route(self.conn, workflow_id, instance_id)]
            marks = ",".join("?" for _ in route_ids)
            rows = self.conn.execute(
                f"SELECT * FROM knowledge_items WHERE workflow_id=? AND target_instance_id IN ({marks}) "
                "ORDER BY created_at,id", (workflow_id, *route_ids),
            ).fetchall()
        return [{"knowledgeItemId": row["id"], "kind": row["kind"],
                 "title": row["title"], "content": row["content"],
                 "provenance": json.loads(row["provenance_json"])} for row in rows]

    @staticmethod
    def _dataset(row: sqlite3.Row, include_cases: bool = True) -> dict[str, Any]:
        result = {"datasetId": row["id"], "workflowId": row["workflow_id"],
                  "name": row["logical_name"], "version": row["version"],
                  "description": row["description"], "sha256": row["sha256"],
                  "createdAt": row["created_at"]}
        cases = json.loads(row["cases_json"])
        result["caseCount"] = len(cases)
        if include_cases:
            result["cases"] = cases
        return result

    def create_dataset(self, workflow_id: str, *, name: str, description: str,
                       cases: list[dict[str, Any]]) -> dict[str, Any]:
        name = name.strip()
        if not name or not cases:
            raise Validation("dataset name and cases are required")
        ids = [case["id"] for case in cases]
        if len(ids) != len(set(ids)):
            raise Validation("dataset case IDs must be unique")
        with self.tx() as cx:
            self._workflow(cx, workflow_id)
            version = cx.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM datasets WHERE workflow_id=? AND logical_name=?",
                (workflow_id, name),
            ).fetchone()[0]
            dataset_id, created = _id("dataset"), _now()
            cx.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?)",
                       (dataset_id, workflow_id, name, version, description.strip(), _json(cases),
                        _hash(cases), created))
            row = cx.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        return self._dataset(row)

    def list_datasets(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.lock:
            self._workflow(self.conn, workflow_id)
            rows = self.conn.execute(
                "SELECT * FROM datasets WHERE workflow_id=? ORDER BY created_at DESC,id DESC",
                (workflow_id,),
            ).fetchall()
        return [self._dataset(row, False) for row in rows]

    @staticmethod
    def _experiment(row: sqlite3.Row) -> dict[str, Any]:
        return {"experimentId": row["id"], "workflowId": row["workflow_id"],
                "name": row["name"], "datasetId": row["dataset_id"],
                "instanceIds": json.loads(row["instance_ids_json"]),
                "runIds": json.loads(row["run_ids_json"]), "metric": row["metric"],
                "notes": row["notes"], "snapshot": json.loads(row["snapshot_json"]),
                "createdAt": row["created_at"]}

    def create_experiment(self, workflow_id: str, *, name: str, dataset_id: str,
                          instance_ids: list[str], run_ids: list[str], metric: str,
                          notes: str) -> dict[str, Any]:
        instances = list(dict.fromkeys(instance_ids))
        if not name.strip() or not instances:
            raise Validation("experiment name and instances are required")
        with self.tx() as cx:
            self._workflow(cx, workflow_id)
            dataset = cx.execute("SELECT * FROM datasets WHERE workflow_id=? AND id=?",
                                 (workflow_id, dataset_id)).fetchone()
            if not dataset:
                raise Validation("experiment dataset was not found")
            for instance_id in instances:
                self._instance(cx, workflow_id, instance_id)
            runs = []
            for run_id in list(dict.fromkeys(run_ids)):
                run = cx.execute("SELECT * FROM agent_runs WHERE workflow_id=? AND id=?",
                                 (workflow_id, run_id)).fetchone()
                if not run or run["instance_id"] not in instances:
                    raise Validation("experiment run does not belong to a selected instance")
                runs.append({"runId": run_id, "instanceId": run["instance_id"],
                             "status": run["status"], "objective": run["objective"],
                             "modelSnapshot": json.loads(run["model_snapshot_json"]),
                             "finalAnswer": run["final_answer"], "errorCode": run["error_code"]})
            snapshot = {"dataset": {"datasetId": dataset_id, "name": dataset["logical_name"],
                                     "version": dataset["version"], "sha256": dataset["sha256"]},
                        "instances": [{"instanceId": item, "route": [row["id"] for row in self._route(cx, workflow_id, item)]}
                                      for item in instances],
                        "runs": runs}
            experiment_id, created = _id("experiment"), _now()
            cx.execute("INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (experiment_id, workflow_id, name.strip(), dataset_id, _json(instances),
                        _json([run["runId"] for run in runs]), metric.strip(), notes.strip(),
                        _json(snapshot), created))
            row = cx.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        return self._experiment(row)

    def list_experiments(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.lock:
            self._workflow(self.conn, workflow_id)
            rows = self.conn.execute(
                "SELECT * FROM experiments WHERE workflow_id=? ORDER BY created_at DESC,id DESC",
                (workflow_id,),
            ).fetchall()
        return [self._experiment(row) for row in rows]
