from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


LATEST_GRAPH_SCHEMA_VERSION = 7
LATEST_RUNTIME_SCHEMA_VERSION = 3


class DatabaseSchemaError(RuntimeError):
    """Raised before migration when the recorded schema history is unsafe."""

    def __init__(self, code: str, message: str, *, graph_versions: tuple[int, ...] = (),
                 runtime_versions: tuple[int, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.graph_versions = graph_versions
        self.runtime_versions = runtime_versions


def _recorded_versions(conn: sqlite3.Connection, table: str) -> tuple[int, ...]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if exists is None:
        return ()
    return tuple(int(row[0]) for row in conn.execute(
        f"SELECT version FROM {table} ORDER BY version"
    ).fetchall())


def inspect_schema_versions(conn: sqlite3.Connection) -> dict[str, object]:
    graph = _recorded_versions(conn, "schema_migrations")
    runtime = _recorded_versions(conn, "runtime_schema_migrations")
    return {
        "graphVersions": graph,
        "runtimeVersions": runtime,
        "graphVersion": graph[-1] if graph else 0,
        "runtimeVersion": runtime[-1] if runtime else 0,
    }


def assert_schema_compatible(conn: sqlite3.Connection) -> dict[str, object]:
    versions = inspect_schema_versions(conn)
    graph = versions["graphVersions"]
    runtime = versions["runtimeVersions"]
    assert isinstance(graph, tuple) and isinstance(runtime, tuple)
    for label, recorded, latest in (
        ("graph", graph, LATEST_GRAPH_SCHEMA_VERSION),
        ("runtime", runtime, LATEST_RUNTIME_SCHEMA_VERSION),
    ):
        if recorded and recorded != tuple(range(1, recorded[-1] + 1)):
            raise DatabaseSchemaError(
                "databaseSchemaHistoryInvalid",
                f"The {label} migration history is not contiguous: {recorded}",
                graph_versions=graph, runtime_versions=runtime,
            )
        if recorded and recorded[-1] > latest:
            raise DatabaseSchemaError(
                "databaseSchemaTooNew",
                f"The database {label} schema is version {recorded[-1]}, "
                f"but this build only supports up to {latest}",
                graph_versions=graph, runtime_versions=runtime,
            )
    return versions


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

V6 = """
CREATE INDEX IF NOT EXISTS idx_instances_turn_owner
ON conversation_instances(workflow_id,owner_instance_id,surface_scope,created_at);
"""

V7 = """
ALTER TABLE conversation_instances
ADD COLUMN title_is_generated INTEGER NOT NULL DEFAULT 0
CHECK(title_is_generated IN (0,1));
"""

V8 = """
CREATE TABLE IF NOT EXISTS chat_requests(
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('started','completed','failed','cancelled')),
    user_message_id INTEGER REFERENCES local_messages(id) ON DELETE SET NULL,
    assistant_message_id INTEGER REFERENCES local_messages(id) ON DELETE SET NULL,
    result_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY(workflow_id,instance_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_chat_requests_status ON chat_requests(status,updated_at);
CREATE TABLE IF NOT EXISTS message_response_details(
    message_id INTEGER PRIMARY KEY REFERENCES local_messages(id) ON DELETE CASCADE,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Large text attachments are stored separately from the message envelope.  As
# with chat_requests, this is an additive Core Service table and does not
# change the host-facing graph snapshot schema.
ATTACHMENTS_AUXILIARY = """
CREATE TABLE IF NOT EXISTS message_attachments(
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES local_messages(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_text TEXT NOT NULL,
    context_text TEXT,
    context_truncated INTEGER NOT NULL DEFAULT 0 CHECK(context_truncated IN (0,1)),
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('uploaded','bound')),
    storage_key TEXT,
    parse_status TEXT NOT NULL DEFAULT 'ready',
    parser_kind TEXT,
    parse_error_code TEXT,
    parse_error TEXT,
    extracted_characters INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    context_sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    bound_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_message_attachments_route
ON message_attachments(workflow_id,instance_id,status,created_at);
CREATE INDEX IF NOT EXISTS idx_message_attachments_message
ON message_attachments(message_id);
CREATE TABLE IF NOT EXISTS attachment_chunks(
    attachment_id TEXT NOT NULL REFERENCES message_attachments(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    locator TEXT NOT NULL,
    content_text TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    character_count INTEGER NOT NULL,
    PRIMARY KEY(attachment_id,ordinal)
);
CREATE INDEX IF NOT EXISTS idx_attachment_chunks_attachment
ON attachment_chunks(attachment_id,ordinal);
CREATE TABLE IF NOT EXISTS attachment_uploads(
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,
    client_key TEXT NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    chunk_size INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    received_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(status IN ('uploading','assembling','completed','cancelled')),
    attachment_id TEXT REFERENCES message_attachments(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workflow_id,instance_id,client_key)
);
CREATE INDEX IF NOT EXISTS idx_attachment_uploads_route
ON attachment_uploads(workflow_id,instance_id,status,updated_at);

-- A durable trigram index keeps Chinese and code substring search useful
-- without requiring a language-specific tokenizer.  Route ownership remains
-- in message_attachments and is always checked by the query layer.
CREATE VIRTUAL TABLE IF NOT EXISTS attachment_chunks_fts USING fts5(
    attachment_id UNINDEXED,
    ordinal UNINDEXED,
    name,
    locator,
    content_text,
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS attachment_chunks_fts_insert
AFTER INSERT ON attachment_chunks BEGIN
    INSERT INTO attachment_chunks_fts(rowid,attachment_id,ordinal,name,locator,content_text)
    VALUES(
        new.rowid,
        new.attachment_id,
        new.ordinal,
        COALESCE((SELECT name FROM message_attachments WHERE id=new.attachment_id),''),
        new.locator,
        new.content_text
    );
END;
CREATE TRIGGER IF NOT EXISTS attachment_chunks_fts_delete
AFTER DELETE ON attachment_chunks BEGIN
    DELETE FROM attachment_chunks_fts WHERE rowid=old.rowid;
END;
CREATE TRIGGER IF NOT EXISTS attachment_chunks_fts_update
AFTER UPDATE ON attachment_chunks BEGIN
    DELETE FROM attachment_chunks_fts WHERE rowid=old.rowid;
    INSERT INTO attachment_chunks_fts(rowid,attachment_id,ordinal,name,locator,content_text)
    VALUES(
        new.rowid,
        new.attachment_id,
        new.ordinal,
        COALESCE((SELECT name FROM message_attachments WHERE id=new.attachment_id),''),
        new.locator,
        new.content_text
    );
END;

-- Automatic retrieval is frozen per user message.  The plan stores the exact
-- provider-visible excerpt while normalized source rows keep provenance and
-- prevent a cited attachment chunk from being deleted or reparsed underneath
-- an already-recorded answer.
CREATE TABLE IF NOT EXISTS message_retrieval_plans(
    message_id INTEGER PRIMARY KEY REFERENCES local_messages(id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL REFERENCES conversation_instances(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK(mode IN ('automatic')),
    query_text TEXT NOT NULL,
    engine TEXT NOT NULL,
    budget_characters INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    selected_characters INTEGER NOT NULL,
    truncated INTEGER NOT NULL CHECK(truncated IN (0,1)),
    context_text TEXT NOT NULL,
    context_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_retrieval_plans_route
ON message_retrieval_plans(workflow_id,instance_id,created_at);
CREATE TABLE IF NOT EXISTS message_retrieval_sources(
    message_id INTEGER NOT NULL REFERENCES message_retrieval_plans(message_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    attachment_id TEXT NOT NULL,
    chunk_ordinal INTEGER NOT NULL,
    route_instance_id TEXT NOT NULL,
    route_title TEXT NOT NULL,
    name TEXT NOT NULL,
    locator TEXT NOT NULL,
    chunk_sha256 TEXT NOT NULL,
    characters INTEGER NOT NULL,
    included_characters INTEGER NOT NULL,
    score REAL NOT NULL,
    matched_terms_json TEXT NOT NULL,
    PRIMARY KEY(message_id,position),
    FOREIGN KEY(attachment_id,chunk_ordinal)
        REFERENCES attachment_chunks(attachment_id,ordinal) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_message_retrieval_sources_attachment
ON message_retrieval_sources(attachment_id,chunk_ordinal);
"""

# Runtime v2 remains an additive preview and deliberately does not advance the
# conversation-graph schema version.  These tables/columns belong to the Agent
# Runtime journal, not to the graph contract consumed by host adapters.
V9_RUNTIME = """
CREATE TABLE IF NOT EXISTS run_approvals(
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE CASCADE,
    side_effect TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_approvals_run ON run_approvals(run_id,created_at);

CREATE TABLE IF NOT EXISTS model_step_usage(
    step_id TEXT PRIMARY KEY REFERENCES run_steps(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    usage_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_step_usage_run ON model_step_usage(run_id,created_at);
"""

V10_RUNTIME_RELIABILITY = """
CREATE TABLE IF NOT EXISTS tool_effects(
    effect_key TEXT PRIMARY KEY,
    root_run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    arguments_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('prepared','executing','completed','failed','interrupted')),
    output_json TEXT,
    error_code TEXT,
    output_sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_effects_root
ON tool_effects(root_run_id,created_at);
"""

V11_HOST_SAGA_AND_STREAM_RECOVERY = """
CREATE TABLE IF NOT EXISTS host_operation_sagas(
    operation_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    source_instance_id TEXT REFERENCES conversation_instances(id) ON DELETE SET NULL,
    target_instance_id TEXT,
    operation_type TEXT NOT NULL CHECK(operation_type IN ('fork','navigate','inspect','archive','rename')),
    host_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'started','host_succeeded','completed','compensated','orphaned','failed'
    )),
    host_result_json TEXT,
    local_result_json TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(workflow_id,operation_type,idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_host_operation_sagas_status
ON host_operation_sagas(status,updated_at);

CREATE TABLE IF NOT EXISTS chat_stream_events(
    workflow_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workflow_id,instance_id,idempotency_key,sequence),
    FOREIGN KEY(workflow_id,instance_id,idempotency_key)
        REFERENCES chat_requests(workflow_id,instance_id,idempotency_key)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_stream_events_request
ON chat_stream_events(workflow_id,instance_id,idempotency_key,sequence);
"""


def run_migrations(conn: sqlite3.Connection) -> None:
    # Refuse unknown or damaged histories before issuing any DDL. This is the
    # release rollback boundary: an older application must never silently
    # open and mutate a database created by a newer build.
    assert_schema_compatible(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runtime_schema_migrations("
        "version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)"
    )
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
    if 6 not in applied:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_instances)")}
        if "surface_scope" not in columns:
            conn.execute(
                "ALTER TABLE conversation_instances ADD COLUMN surface_scope TEXT NOT NULL "
                "DEFAULT 'workflow' CHECK(surface_scope IN ('workflow','turn'))"
            )
        if "owner_instance_id" not in columns:
            conn.execute(
                "ALTER TABLE conversation_instances ADD COLUMN owner_instance_id TEXT "
                "REFERENCES conversation_instances(id)"
            )

        # Before schema v6 the second-layer fork command reused a normal
        # ConversationInstance. Exact local-turn checkpoints therefore leaked
        # into the top-level workflow graph. Reclassify those rows as internal
        # turn routes and keep their original messages/checkpoints intact.
        candidates = conn.execute(
            "SELECT ci.id,ci.workflow_id,ci.parent_id FROM conversation_instances ci "
            "JOIN checkpoints cp ON cp.id=ci.checkpoint_id "
            "WHERE cp.source_cursor_kind='localUserTurn' ORDER BY ci.created_at,ci.id"
        ).fetchall()
        for instance_id, workflow_id, parent_id in candidates:
            parent = conn.execute(
                "SELECT id,surface_scope,owner_instance_id FROM conversation_instances "
                "WHERE workflow_id=? AND id=?",
                (workflow_id, parent_id),
            ).fetchone()
            if parent:
                owner_id = parent[2] if parent[1] == "turn" else parent[0]
                conn.execute(
                    "UPDATE conversation_instances SET surface_scope='turn',owner_instance_id=? "
                    "WHERE workflow_id=? AND id=?",
                    (owner_id, workflow_id, instance_id),
                )

        # Normalize nested exact-turn rows even when parent and child share the
        # same second-level created_at value and were visited out of order.
        while True:
            changed = conn.execute(
                "UPDATE conversation_instances SET owner_instance_id=("
                "SELECT parent.owner_instance_id FROM conversation_instances parent "
                "WHERE parent.id=conversation_instances.parent_id"
                ") WHERE surface_scope='turn' AND parent_id IN ("
                "SELECT id FROM conversation_instances WHERE surface_scope='turn'"
                ") AND owner_instance_id IS NOT (SELECT parent.owner_instance_id "
                "FROM conversation_instances parent WHERE parent.id=conversation_instances.parent_id)"
            ).rowcount
            if not changed:
                break

        # Any descendants of a migrated internal route belong to the same
        # second-layer canvas even if their older checkpoint used instanceHead.
        while True:
            changed = conn.execute(
                "UPDATE conversation_instances SET surface_scope='turn',owner_instance_id=("
                "SELECT parent.owner_instance_id FROM conversation_instances parent "
                "WHERE parent.id=conversation_instances.parent_id"
                ") WHERE surface_scope='workflow' AND parent_id IN ("
                "SELECT id FROM conversation_instances WHERE surface_scope='turn'"
                ")"
            ).rowcount
            if not changed:
                break
        conn.executescript(V6)
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(6,?)", (_now(),))
    if 7 not in applied:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_instances)")}
        if "title_is_generated" not in columns:
            conn.executescript(V7)
        # Existing titles predate explicit provenance tracking. Treat them as
        # user-owned so an upgrade can never overwrite a historical name.
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(7,?)", (_now(),))
    # Chat request durability is an auxiliary table and does not change the
    # graph schema contract (currently v7). Create it for both fresh and
    # already-migrated databases without advancing the graph schema version.
    conn.executescript(V8)
    conn.executescript(ATTACHMENTS_AUXILIARY)
    attachment_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(message_attachments)")
    }
    attachment_additions = {
        "storage_key": "TEXT",
        "parse_status": "TEXT NOT NULL DEFAULT 'ready'",
        "parser_kind": "TEXT",
        "parse_error_code": "TEXT",
        "parse_error": "TEXT",
        "extracted_characters": "INTEGER NOT NULL DEFAULT 0",
        "chunk_count": "INTEGER NOT NULL DEFAULT 0",
        "context_sources_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, declaration in attachment_additions.items():
        if name not in attachment_columns:
            conn.execute(f"ALTER TABLE message_attachments ADD COLUMN {name} {declaration}")
    conn.execute(
        "UPDATE message_attachments SET extracted_characters=LENGTH(content_text) "
        "WHERE extracted_characters=0 AND content_text<>''"
    )
    # Backfill chunks created before the durable search index existed. Keeping
    # attachment_chunks.rowid as the FTS rowid makes trigger maintenance and
    # integrity checks deterministic across restarts.
    conn.execute(
        "DELETE FROM attachment_chunks_fts WHERE rowid NOT IN "
        "(SELECT rowid FROM attachment_chunks)"
    )
    conn.execute(
        "INSERT INTO attachment_chunks_fts(rowid,attachment_id,ordinal,name,locator,content_text) "
        "SELECT ac.rowid,ac.attachment_id,ac.ordinal,ma.name,ac.locator,ac.content_text "
        "FROM attachment_chunks ac JOIN message_attachments ma ON ma.id=ac.attachment_id "
        "WHERE NOT EXISTS (SELECT 1 FROM attachment_chunks_fts f WHERE f.rowid=ac.rowid)"
    )
    runtime_applied = {
        row[0] for row in conn.execute("SELECT version FROM runtime_schema_migrations")
    }
    if 1 not in runtime_applied:
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_runs)")}
        if "root_run_id" not in run_columns:
            conn.execute("ALTER TABLE agent_runs ADD COLUMN root_run_id TEXT")
        if "parent_run_id" not in run_columns:
            conn.execute("ALTER TABLE agent_runs ADD COLUMN parent_run_id TEXT")
        if "attempt_number" not in run_columns:
            conn.execute("ALTER TABLE agent_runs ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 1")
        tool_columns = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
        if "provider_call_id" not in tool_columns:
            conn.execute("ALTER TABLE tool_calls ADD COLUMN provider_call_id TEXT")
        conn.executescript(V9_RUNTIME)
        conn.execute(
            "UPDATE agent_runs SET root_run_id=id WHERE root_run_id IS NULL OR root_run_id=''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_root "
            "ON agent_runs(root_run_id,attempt_number)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_parent ON agent_runs(parent_run_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_calls_provider "
            "ON tool_calls(run_id,provider_call_id) WHERE provider_call_id IS NOT NULL"
        )
        # The marker is deliberately last so an interrupted/partial migration
        # safely reruns all idempotent checks on the next startup.
        conn.execute(
            "INSERT INTO runtime_schema_migrations(version,applied_at) VALUES(1,?)",
            (_now(),),
        )
    if 2 not in runtime_applied:
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_runs)")}
        run_additions = {
            "lease_owner": "TEXT",
            "lease_expires_at": "TEXT",
            "last_heartbeat_at": "TEXT",
            "execution_phase": "TEXT",
        }
        for name, declaration in run_additions.items():
            if name not in run_columns:
                conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {name} {declaration}")
        tool_columns = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
        if "effect_key" not in tool_columns:
            conn.execute("ALTER TABLE tool_calls ADD COLUMN effect_key TEXT")
        conn.executescript(V10_RUNTIME_RELIABILITY)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_lease "
            "ON agent_runs(status,lease_expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_calls_effect ON tool_calls(effect_key)"
        )
        conn.execute(
            "INSERT INTO runtime_schema_migrations(version,applied_at) VALUES(2,?)",
            (_now(),),
        )
    if 3 not in runtime_applied:
        conn.executescript(V11_HOST_SAGA_AND_STREAM_RECOVERY)
        # The marker is last so a process interrupted during the additive DDL
        # can safely replay the idempotent statements on the next startup.
        conn.execute(
            "INSERT INTO runtime_schema_migrations(version,applied_at) VALUES(3,?)",
            (_now(),),
        )
    conn.commit()
