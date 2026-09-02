# WeavePath backend

Python 3.12 graph core and FastAPI API for route-aware conversation workflows.

```powershell
python -m pip install -e ".[test]"
uvicorn api.app:create_app --factory --reload
python -m pytest
```

The API uses Uvicorn's application-factory mode. Importing `api.app` only
defines the factory and does not open or create the default SQLite database;
the database is opened when Uvicorn calls `create_app` during server startup.
The official factory path takes a non-blocking operating-system lock beside the
selected database before `GraphStore` opens it. This serializes schema migration
and startup Run recovery, is released by normal lifespan shutdown or an OS
process exit, and leaves the harmless lock file in place. A second backend for
the same database exits with `databaseInstanceAlreadyRunning` instead of
recovering the first process's active Runs. Uvicorn's `--reload` supervisor only
imports the factory module; its serving child owns the lock.

Route-to-Agent Run v1 still supports only one local API process, so do not add a
Uvicorn `--workers` value greater than one. Cross-process Run owner/lease and
worker coordination remain future work. Passing an existing `GraphStore` to
`create_app` is an explicit dependency-injection/testing path and intentionally
does not take this lock. The lock-free `open_default_store()` helper is likewise
for one-shot callers, not an alternative production server entry point.
Every server instance must use the same canonical `WEAVEPATH_DB` spelling;
hard-link, mapped-drive, or UNC aliases for one SQLite file can produce distinct
sidecar paths and are outside the v1 lock guarantee.

The SQLite database defaults to `%LOCALAPPDATA%/WeavePath/data/workspace.db` on
Windows, `$XDG_DATA_HOME/weavepath/data/workspace.db` when XDG data is configured,
or `~/.local/share/weavepath/data/workspace.db` elsewhere. Set `WEAVEPATH_DATA_DIR`
to choose the containing directory, or `WEAVEPATH_DB` to point directly to a
database file. The new variables take priority over the backward-compatible
`COTHINKER_DATA_DIR` and `COTHINKER_WORKFLOW_DB` variables.

Existing installations remain in place: when the new default database does not
exist but a database exists in the former `CoThinker Workspace` or
`co-thinker-workspace` data directory, WeavePath opens that old database directly.
It does not copy, rename, or delete it. In a restricted sandbox that
denies the user data directory, startup falls back to the operating-system temp
directory, never the source tree. Every fork stores an immutable checkpoint
anchor and creation-time snapshot of the parent's effective messages for audit.
Runtime context follows the parent route dynamically, so later parent messages
and edits are visible to the child while sibling routes remain excluded. Graph
mutations and content writes use independent revisions.
Each node's `contentRevision` changes only when that instance receives a local
message. Workflow `eventRevision` is the monotonic global content-event sequence
formerly stored in the `workflows.content_revision` column; it is not a route
memory version.

The optional AI endpoint uses an OpenAI-compatible `/v1/chat/completions`
provider. Set `WEAVEPATH_LLM_BASE_URL` and `WEAVEPATH_LLM_MODEL`; optionally set
`WEAVEPATH_LLM_API_KEY`, `WEAVEPATH_LLM_TIMEOUT`, and
`WEAVEPATH_LLM_SYSTEM_PROMPT`. The former `COTHINKER_LLM_*` names remain supported
as lower-priority compatibility aliases, and `OPENAI_API_KEY` remains the final
API-key fallback. The chat route builds context
from the selected conversation instance and its current parent route, excluding
sibling routes. Without configuration, `/api/v1/ai/status`
reports record-only mode and the API never fabricates an assistant message.

Runtime model settings are available at `/api/v1/ai/settings`. API keys are
write-only, retained only in process memory (or read from environment), and are
never returned or written to `model-settings.json`. Choose `persistence:
"memory"` for process-only configuration or `"local"` to persist only the
non-secret provider fields. `clearApiKey: true` explicitly clears the current
in-memory key; omitting `apiKey` or sending an empty value preserves it. Draft
connection validation uses `POST /api/v1/ai/settings/validate` without saving
the draft. `GET /api/v1/ai/models` discovers models for the current settings.
Plain HTTP providers are accepted only on loopback hosts; remote providers must
use HTTPS.

API responses are JSON objects rather than a generic `data` envelope. Graph
responses expose `workflowId`, revisions, and `nodes`; mutations return their
result object directly. All public request and response fields use camelCase.
