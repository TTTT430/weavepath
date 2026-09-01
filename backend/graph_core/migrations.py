from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


V1 = """
CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,name TEXT NOT NULL,root_instance_id TEXT,active_instance_id TEXT,graph_revision INTEGER NOT NULL DEFAULT 0,content_revision INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topics(id TEXT NOT NULL,workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,name TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(workflow_id,id));
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,source_instance_id TEXT,source_content_revision INTEGER NOT NULL,messages_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversation_instances(id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,topic_id TEXT NOT NULL,parent_id TEXT REFERENCES conversation_instances(id),checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),title TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','pruned')),provider TEXT NOT NULL,provider_conversation_id TEXT,content_revision INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,FOREIGN KEY(workflow_id,topic_id) REFERENCES topics(workflow_id,id));
CREATE INDEX IF NOT EXISTS idx_instances_workflow_parent ON conversation_instances(workflow_id,parent_id);
CREATE INDEX IF NOT EXISTS idx_instances_workflow_topic ON conversation_instances(workflow_id,topic_id);
CREATE TABLE IF NOT EXISTS local_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),content TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_instance ON local_messages(instance_id,id);
CREATE TABLE IF NOT EXISTS tombstones(workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,instance_id TEXT PRIMARY KEY REFERENCES conversation_instances(id),pruned_at TEXT NOT NULL,prune_command_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,idempotency_key TEXT NOT NULL,command_type TEXT NOT NULL,request_json TEXT NOT NULL,response_json TEXT,status TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT,UNIQUE(workflow_id,idempotency_key));
"""

V2 = """
CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,status TEXT NOT NULL,input_content_revision INTEGER NOT NULL,context_snapshot_json TEXT NOT NULL,context_sha256 TEXT NOT NULL,model_snapshot_json TEXT NOT NULL,request_json TEXT NOT NULL,request_sha256 TEXT NOT NULL,idempotency_key TEXT NOT NULL,objective TEXT NOT NULL,constraints_json TEXT NOT NULL,deliverables_json TEXT NOT NULL,acceptance_checks_json TEXT NOT NULL,final_message_id INTEGER,error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(workflow_id,instance_id,idempotency_key));
CREATE INDEX IF NOT EXISTS idx_agent_runs_instance ON agent_runs(workflow_id,instance_id,created_at);
CREATE TABLE IF NOT EXISTS run_steps(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,sequence INTEGER NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,completed_at TEXT,UNIQUE(run_id,sequence));
CREATE TABLE IF NOT EXISTS run_events(run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,sequence INTEGER NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(run_id,sequence));
CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,step_id TEXT NOT NULL REFERENCES run_steps(id) ON DELETE CASCADE,tool_name TEXT NOT NULL,tool_version TEXT NOT NULL,arguments_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT);
CREATE TABLE IF NOT EXISTS tool_results(id TEXT PRIMARY KEY,tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE CASCADE,output_json TEXT,error_code TEXT,duration_ms INTEGER NOT NULL,output_sha256 TEXT,created_at TEXT NOT NULL);
"""

V3 = """
ALTER TABLE agent_runs ADD COLUMN final_answer TEXT;
"""

V4 = """
ALTER TABLE checkpoints ADD COLUMN source_cursor_kind TEXT;
ALTER TABLE checkpoints ADD COLUMN source_cursor_value TEXT;
"""

V5 = """
CREATE TABLE IF NOT EXISTS artifacts(
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    instance_id TEXT REFERENCES conversation_instances(id) ON DELETE SET NULL,
    run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL,
    logical_name TEXT NOT NULL,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    content_text TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(workflow_id,logical_name,version)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_workflow ON artifacts(workflow_id,created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);

CREATE TABLE IF NOT EXISTS knowledge_merges(
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    target_instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,
    source_instance_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('accepted')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_items(
    id TEXT PRIMARY KEY,
    merge_id TEXT NOT NULL REFERENCES knowledge_merges(id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    target_instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,
    source_instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,
    source_run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK(kind IN ('conclusion','decision','fact','constraint')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_target ON knowledge_items(workflow_id,target_instance_id,created_at);
CREATE TABLE IF NOT EXISTS knowledge_merge_artifacts(
    merge_id TEXT NOT NULL REFERENCES knowledge_merges(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    PRIMARY KEY(merge_id,artifact_id)
);

CREATE TABLE IF NOT EXISTS datasets(
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    logical_name TEXT NOT NULL,
    version INTEGER NOT NULL,
    description TEXT NOT NULL,
    cases_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(workflow_id,logical_name,version)
);
CREATE INDEX IF NOT EXISTS idx_datasets_workflow ON datasets(workflow_id,created_at);

CREATE TABLE IF NOT EXISTS experiments(
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    instance_ids_json TEXT NOT NULL,
    run_ids_json TEXT NOT NULL,
    metric TEXT NOT NULL,
    notes TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiments_workflow ON experiments(workflow_id,created_at);
"""


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    if 1 not in applied:
        conn.executescript(V1)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_instances)")}
        if "content_revision" not in columns:
            conn.execute("ALTER TABLE conversation_instances ADD COLUMN content_revision INTEGER NOT NULL DEFAULT 0")
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(1,?)", (_now(),))
    if 2 not in applied:
        conn.executescript(V2)
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(2,?)", (_now(),))
    if 3 not in applied:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_runs)")}
        if "final_answer" not in columns:
            conn.executescript(V3)
        conn.execute(
            "UPDATE agent_runs SET final_answer=("
            "SELECT content FROM local_messages "
            "WHERE local_messages.id=agent_runs.final_message_id "
            "AND local_messages.workflow_id=agent_runs.workflow_id "
            "AND local_messages.instance_id=agent_runs.instance_id"
            ") WHERE final_answer IS NULL AND final_message_id IS NOT NULL"
        )
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(3,?)", (_now(),))
    if 4 not in applied:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(checkpoints)")}
        if "source_cursor_kind" not in columns:
            conn.execute("ALTER TABLE checkpoints ADD COLUMN source_cursor_kind TEXT")
        if "source_cursor_value" not in columns:
            conn.execute("ALTER TABLE checkpoints ADD COLUMN source_cursor_value TEXT")
        # Existing child checkpoints were all created from the source
        # instance head. Preserve that fact as an explicit, auditable cursor.
        conn.execute(
            "UPDATE checkpoints SET source_cursor_kind='instanceHead', "
            "source_cursor_value=CAST(source_content_revision AS TEXT) "
            "WHERE source_instance_id IS NOT NULL AND source_cursor_kind IS NULL"
        )
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(4,?)", (_now(),))
    if 5 not in applied:
        conn.executescript(V5)
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(5,?)", (_now(),))
    conn.commit()
