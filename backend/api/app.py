from __future__ import annotations

import errno
import json
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Lock
from typing import BinaryIO, Callable, Literal, TypeVar

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agent_runtime import (AgentModelPort, AgentRunError, AgentRunRepository, AgentRuntimeService,
                           OpenAICompatibleAgentAdapter, calculator_registry)
from api.llm import LLMClient, LLMUnavailable, OpenAICompatibleLLM
from api.model_settings import RuntimeModelSettings
from engineering import EngineeringRepository
from graph_core import Conflict, GraphStore, NotFound, Validation


_StoreResource = TypeVar("_StoreResource")
_NARROW_SQLITE_ACCESS_ERRORS = {
    "unable to open database file",
    "attempt to write a readonly database",
}
_NARROW_FILESYSTEM_ACCESS_ERRNOS = {
    errno.EACCES,
    errno.EPERM,
    getattr(errno, "EROFS", errno.EACCES),
}


class DatabaseInstanceLockError(RuntimeError):
    """Raised when another WeavePath backend owns the selected database."""

    code = "databaseInstanceAlreadyRunning"

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        super().__init__(
            f"{self.code}: Another WeavePath backend is already using database "
            f"'{self.database_path}'. Stop that backend before starting a second instance."
        )


class _ProcessFileLock:
    """A non-blocking, process-scoped file lock that works on Windows and POSIX.

    The small lock file is deliberately retained after release. Ownership is
    represented by the operating-system lock, so a process crash releases it
    without an unsafe unlink race.
    """

    def __init__(self, database_path: str | Path) -> None:
        database = Path(database_path).resolve(strict=False)
        self.database_path = str(database)
        self.path = Path(str(database) + ".weavepath.lock")
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            # msvcrt locks a byte range and requires that byte to exist.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                busy_errnos = {errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", errno.EACCES)}
                if exc.errno in busy_errnos:
                    raise DatabaseInstanceLockError(self.database_path) from None
                raise
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def default_database_path() -> str:
    configured = os.getenv("WEAVEPATH_DB")
    if configured:
        return configured
    explicit_dir = os.getenv("WEAVEPATH_DATA_DIR")
    if explicit_dir:
        return str(Path(explicit_dir) / "workspace.db")
    configured = os.getenv("COTHINKER_WORKFLOW_DB")
    if configured:
        return configured
    explicit_dir = os.getenv("COTHINKER_DATA_DIR")
    if explicit_dir:
        return str(Path(explicit_dir) / "workspace.db")

    local_app_data = os.getenv("LOCALAPPDATA")
    xdg_data_home = os.getenv("XDG_DATA_HOME")
    if local_app_data:
        base = Path(local_app_data)
        current = base / "WeavePath" / "data" / "workspace.db"
        legacy = [
            base / "CoThinker Workspace" / "data" / "workspace.db",
            base / "co-thinker-workspace" / "data" / "workspace.db",
        ]
    else:
        base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
        current = base / "weavepath" / "data" / "workspace.db"
        legacy = [
            base / "co-thinker-workspace" / "data" / "workspace.db",
            base / "CoThinker Workspace" / "data" / "workspace.db",
        ]
    if not current.exists():
        for candidate in legacy:
            if candidate.exists():
                return str(candidate)
    return str(current)


def _temp_database_path() -> Path:
    return Path(tempfile.gettempdir()) / "WeavePath" / "data" / "workspace.db"


def _is_narrow_access_failure(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        return str(exc).lower() in _NARROW_SQLITE_ACCESS_ERRORS
    return isinstance(exc, OSError) and exc.errno in _NARROW_FILESYSTEM_ACCESS_ERRNOS


def _open_with_narrow_temp_fallback(
    opener: Callable[[str | Path], _StoreResource],
) -> _StoreResource:
    primary = default_database_path()
    try:
        return opener(primary)
    except (OSError, sqlite3.OperationalError) as exc:
        # sqlite reports an inaccessible existing directory as OperationalError
        # rather than PermissionError. Lock files can fail with the equivalent
        # filesystem errno. Do not hide contention, corruption, or failed
        # migrations behind an apparently empty temporary workspace.
        if not _is_narrow_access_failure(exc):
            raise
        fallback = _temp_database_path()
        if os.path.normcase(os.path.abspath(primary)) == os.path.normcase(os.path.abspath(fallback)):
            raise
        return opener(fallback)


def open_default_store() -> GraphStore:
    """Open the default store with the legacy narrow temp fallback.

    Runtime ownership uses ``_open_locked_default_store`` below. Keeping this
    helper lock-free makes it suitable for one-shot callers and preserves its
    existing public behavior.
    """

    return _open_with_narrow_temp_fallback(GraphStore)


def _open_locked_store(database_path: str | Path) -> tuple[GraphStore, _ProcessFileLock | None]:
    if str(database_path) == ":memory:":
        return GraphStore(":memory:"), None
    instance_lock = _ProcessFileLock(database_path)
    instance_lock.acquire()
    try:
        # GraphStore performs all migrations in its constructor, after the
        # process lock has been acquired.
        return GraphStore(database_path), instance_lock
    except BaseException:
        instance_lock.release()
        raise


def _open_locked_default_store() -> tuple[GraphStore, _ProcessFileLock | None]:
    return _open_with_narrow_temp_fallback(_open_locked_store)


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CreateWorkflow(CamelModel):
    name: str = ""
    root_title: str = Field("", alias="rootTitle")
    root_topic_id: str | None = Field(None, alias="rootTopicId")
    provider: str = "local"
    root_instance_id: str | None = Field(None, alias="rootInstanceId")
    provider_conversation_id: str | None = Field(None, alias="providerConversationId")


class MessageInput(CamelModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatInput(CamelModel):
    content: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str | None = Field(None, alias="idempotencyKey", max_length=200)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def idempotency_key_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("idempotencyKey must not be blank")
        return value.strip() if value is not None else None


class RegenerateMessageInput(ChatInput):
    expected_revision: int = Field(alias="expectedRevision", ge=0)


class CreateAgentRunInput(CamelModel):
    objective: str = Field(min_length=1, max_length=20_000)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    deliverables: list[str] = Field(default_factory=list, max_length=50)
    acceptance_checks: list[str] = Field(default_factory=list, alias="acceptanceChecks", max_length=50)
    expected_content_revision: int = Field(alias="expectedContentRevision", ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=200)

    @field_validator("objective")
    @classmethod
    def objective_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
        return value.strip()

    @field_validator("constraints", "deliverables", "acceptance_checks")
    @classmethod
    def brief_items_are_bounded(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("execution brief items must not be blank")
        if any(len(value) > 2_000 for value in cleaned):
            raise ValueError("execution brief items must be at most 2000 characters")
        return cleaned

    @field_validator("idempotency_key")
    @classmethod
    def idempotency_key_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idempotencyKey must not be blank")
        return value.strip()


class ModelSettingsInput(CamelModel):
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(None, alias="apiKey")
    timeout_seconds: float = Field(60.0, alias="timeoutSeconds", ge=1, le=300)
    system_prompt: str = Field("", alias="systemPrompt", max_length=20_000)
    persistence: Literal["memory", "local"] = "memory"
    clear_api_key: bool = Field(False, alias="clearApiKey")


class ModelSettingsValidationInput(ModelSettingsInput):
    # Discovery only needs a provider endpoint. Saving still requires a model.
    model: str = Field("", max_length=200)


class ForkInput(CamelModel):
    title: str | None = Field(None, max_length=240)
    topic_id: str | None = Field(None, alias="topicId")
    provider: str | None = None
    instance_id: str | None = Field(None, alias="instanceId")
    provider_conversation_id: str | None = Field(None, alias="providerConversationId")
    initial_message: str | None = Field(None, alias="initialMessage", max_length=20_000)
    anchor_message_id: int | None = Field(None, alias="anchorMessageId", ge=1)
    expected_content_revision: int | None = Field(None, alias="expectedContentRevision", ge=0)
    idempotency_key: str | None = Field(None, alias="idempotencyKey", max_length=200)


class ForkChatInput(CamelModel):
    title: str | None = Field(None, max_length=240)
    topic_id: str | None = Field(None, alias="topicId", max_length=240)
    initial_message: str | None = Field(None, alias="initialMessage", max_length=20_000)
    anchor_message_id: int | None = Field(None, alias="anchorMessageId", ge=1)
    expected_content_revision: int | None = Field(None, alias="expectedContentRevision", ge=0)
    idempotency_key: str | None = Field(None, alias="idempotencyKey", max_length=200)


class RenameInstanceInput(CamelModel):
    title: str = Field(min_length=1, max_length=240)
    expected_revision: int = Field(alias="expectedRevision", ge=0)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class RenameWorkflowInput(CamelModel):
    name: str = Field(min_length=1, max_length=240)
    expected_revision: int = Field(alias="expectedRevision", ge=0)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


class ActivateInput(CamelModel):
    preference_key: str | None = Field(None, alias="preferenceKey")


class PrunePlanInput(CamelModel):
    allow_root: bool = Field(False, alias="allowRoot")


class PruneCommitInput(PrunePlanInput):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1)


class ArtifactInput(CamelModel):
    name: str = Field(min_length=1, max_length=240)
    kind: str = Field(default="text", min_length=1, max_length=80)
    mime_type: str = Field(default="text/plain", alias="mimeType", min_length=1, max_length=200)
    content: str = Field(default="", max_length=1_000_000)
    instance_id: str | None = Field(None, alias="instanceId", max_length=240)
    run_id: str | None = Field(None, alias="runId", max_length=240)
    metadata: dict[str, object] = Field(default_factory=dict)


class ComparisonInput(CamelModel):
    instance_ids: list[str] = Field(alias="instanceIds", min_length=2, max_length=4)


class KnowledgeSelection(CamelModel):
    source_instance_id: str = Field(alias="sourceInstanceId", min_length=1, max_length=240)
    source_run_id: str | None = Field(None, alias="sourceRunId", max_length=240)
    kind: Literal["conclusion", "decision", "fact", "constraint"] = "conclusion"
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=20_000)


class KnowledgeMergeInput(CamelModel):
    target_instance_id: str = Field(alias="targetInstanceId", min_length=1, max_length=240)
    source_instance_ids: list[str] = Field(alias="sourceInstanceIds", min_length=1, max_length=4)
    items: list[KnowledgeSelection] = Field(default_factory=list, max_length=50)
    artifact_ids: list[str] = Field(default_factory=list, alias="artifactIds", max_length=50)


class DatasetCase(CamelModel):
    id: str = Field(min_length=1, max_length=240)
    input: str = Field(min_length=1, max_length=20_000)
    expected: str | None = Field(None, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=50)


class DatasetInput(CamelModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=20_000)
    cases: list[DatasetCase] = Field(min_length=1, max_length=5_000)


class ExperimentInput(CamelModel):
    name: str = Field(min_length=1, max_length=240)
    dataset_id: str = Field(alias="datasetId", min_length=1, max_length=240)
    instance_ids: list[str] = Field(alias="instanceIds", min_length=1, max_length=20)
    run_ids: list[str] = Field(default_factory=list, alias="runIds", max_length=100)
    metric: str = Field(default="manual-review", max_length=240)
    notes: str = Field(default="", max_length=20_000)


def create_app(store: GraphStore | None = None, llm_client: LLMClient | None = None,
               model_settings: RuntimeModelSettings | None = None,
               agent_model: AgentModelPort | None = None) -> FastAPI:
    owned = store is None
    instance_lock: _ProcessFileLock | None = None
    if owned:
        graph_store, instance_lock = _open_locked_default_store()
    else:
        graph_store = store
    try:
        settings_path = (Path(graph_store.db_path).with_name("model-settings.json")
                         if graph_store.db_path != ":memory:"
                         else Path(default_database_path()).with_name("model-settings.json"))
        settings = model_settings or RuntimeModelSettings(settings_path)
        llm: LLMClient = llm_client or settings
        run_repository = AgentRunRepository(graph_store._conn, graph_store._lock)
        engineering = EngineeringRepository(graph_store._conn, graph_store._lock)
        if agent_model is None:
            if isinstance(llm, OpenAICompatibleLLM):
                agent_model = OpenAICompatibleAgentAdapter(lambda: llm)
            elif hasattr(llm, "_client"):
                agent_model = OpenAICompatibleAgentAdapter(llm._client)
        run_service = (AgentRuntimeService(graph_store, run_repository, agent_model, calculator_registry(),
                                           engineering=engineering)
                       if agent_model is not None else None)
    except BaseException:
        if owned:
            try:
                graph_store.close()
            finally:
                if instance_lock is not None:
                    instance_lock.release()
        raise

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            run_repository.recover_interrupted()
            yield
        finally:
            if owned:
                try:
                    graph_store.close()
                finally:
                    if instance_lock is not None:
                        instance_lock.release()

    app = FastAPI(title="WeavePath API", version="0.1.0", lifespan=lifespan)
    app.state.store = graph_store
    app.state.model_settings = settings
    app.state.agent_runs = run_repository
    app.state.engineering = engineering
    # Chat request identity is deliberately process-local.  The graph remains
    # the source of truth for messages; this registry only prevents duplicate
    # clicks and lets a streaming request be cancelled without inventing a
    # second transcript table.
    chat_state_lock = Lock()
    chat_active: dict[str, Event] = {}
    chat_completed: dict[str, tuple[str, dict[str, object]]] = {}
    app.state.chat_active = chat_active

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound):
        return JSONResponse({"code": "notFound", "error": str(exc)}, 404)

    @app.exception_handler(Conflict)
    async def conflict(_: Request, exc: Conflict):
        return JSONResponse({"code": "conflict", "error": str(exc)}, 409)

    @app.exception_handler(Validation)
    async def invalid(_: Request, exc: Validation):
        return JSONResponse({"code": "validationError", "error": str(exc)}, 422)

    @app.exception_handler(LLMUnavailable)
    async def llm_unavailable(_: Request, exc: LLMUnavailable):
        return JSONResponse({"code": exc.code, "error": str(exc)}, exc.status_code)

    @app.exception_handler(AgentRunError)
    async def agent_run_error(_: Request, exc: AgentRunError):
        payload = {"code": exc.code, "error": str(exc)}
        if exc.run_id:
            payload["runId"] = exc.run_id
        return JSONResponse(payload, exc.status_code)

    @app.exception_handler(ValueError)
    async def bad_value(_: Request, exc: ValueError):
        return JSONResponse({"code": "validationError", "error": str(exc)}, 422)

    @app.exception_handler(RequestValidationError)
    async def request_invalid(_: Request, exc: RequestValidationError):
        # Never echo Pydantic's raw `input`: it may contain a write-only API key.
        fields = [".".join(str(item) for item in error.get("loc", [])[1:]) for error in exc.errors()]
        return JSONResponse({"code": "validationError", "error": "Invalid request",
                             "fields": [field for field in fields if field]}, 422)

    prefix = "/api/v1"

    def route_context(workflow_id: str, instance_id: str,
                      messages: list[dict[str, object]]) -> list[dict[str, object]]:
        accepted = engineering.accepted_knowledge(workflow_id, instance_id)
        if not accepted:
            return messages
        return [*messages, {"role": "system", "content": json.dumps({
            "acceptedRouteKnowledge": accepted,
            "instruction": "Use only these explicitly accepted cross-route facts; do not infer sibling transcripts.",
        }, ensure_ascii=False)}]

    @app.get(prefix + "/health")
    def health():
        return {"ok": True, "service": "weavepath", "version": app.version,
                "schemaVersion": 7, "aiConfigured": bool(llm.status()["configured"])}

    @app.get(prefix + "/ai/status")
    def ai_status():
        return llm.status()

    @app.get(prefix + "/ai/settings")
    def get_ai_settings():
        return settings.status()

    @app.put(prefix + "/ai/settings")
    def put_ai_settings(body: ModelSettingsInput):
        return settings.configure(
            base_url=body.base_url, model=body.model,
            api_key=body.api_key.get_secret_value() if body.api_key is not None else None,
            timeout_seconds=body.timeout_seconds, system_prompt=body.system_prompt,
            persistence=body.persistence, clear_api_key=body.clear_api_key,
        )

    @app.delete(prefix + "/ai/settings")
    def delete_ai_settings():
        return settings.reset()

    @app.get(prefix + "/ai/models")
    def discover_ai_models():
        models = settings.discover_models()
        return {"models": models, "count": len(models)}

    @app.post(prefix + "/ai/settings/validate")
    def validate_ai_settings(body: ModelSettingsValidationInput):
        return settings.validate_connection(
            base_url=body.base_url, model=body.model,
            api_key=body.api_key.get_secret_value() if body.api_key is not None else None,
            timeout_seconds=body.timeout_seconds, system_prompt=body.system_prompt,
        )

    @app.post(prefix + "/workflows", status_code=201)
    def create(body: CreateWorkflow):
        return graph_store.create_workflow(**body.model_dump(by_alias=False))

    @app.get(prefix + "/workflows")
    def list_workflows():
        return graph_store.list_workflows()

    @app.get(prefix + "/workflows/{workflow_id}/graph")
    def graph(workflow_id: str):
        return graph_store.get_graph(workflow_id)

    @app.get(prefix + "/workflows/{workflow_id}/instances/{instance_id}/messages")
    def messages(workflow_id: str, instance_id: str,
                 scope: Literal["local", "effective"] = "effective"):
        return graph_store.list_messages(workflow_id, instance_id, scope=scope)

    @app.get(prefix + "/workflows/{workflow_id}/instances/{instance_id}/turns")
    def turns(workflow_id: str, instance_id: str):
        return graph_store.list_turns(workflow_id, instance_id)

    @app.get(prefix + "/workflows/{workflow_id}/instances/{instance_id}/turn-tree")
    def turn_tree(workflow_id: str, instance_id: str):
        return graph_store.list_turn_tree(workflow_id, instance_id)

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/messages", status_code=201)
    def append(workflow_id: str, instance_id: str, body: MessageInput):
        return graph_store.append_message(workflow_id, instance_id, **body.model_dump())

    def _chat_identity(workflow_id: str, instance_id: str, body: ChatInput) -> tuple[str | None, str]:
        key = body.idempotency_key.strip() if body.idempotency_key else None
        return key, f"{workflow_id}:{instance_id}:{key or ''}:{body.content}"

    def _body_with_header_key(request: Request, body: ChatInput) -> ChatInput:
        if body.idempotency_key:
            return body
        header = request.headers.get("Idempotency-Key", "").strip()
        if len(header) > 200:
            raise Validation("idempotencyKey must be at most 200 characters")
        return body.model_copy(update={"idempotency_key": header}) if header else body

    def _chat_begin(workflow_id: str, instance_id: str, body: ChatInput) -> tuple[str | None, str, Event | None, dict[str, object] | None]:
        key, signature = _chat_identity(workflow_id, instance_id, body)
        if key is None:
            return None, signature, None, None
        scoped = f"{workflow_id}:{instance_id}:{key}"
        with chat_state_lock:
            previous = chat_completed.get(scoped)
            if previous:
                if previous[0] != signature:
                    raise Conflict("idempotencyKey was already used with different content")
                return key, signature, None, previous[1]
            if scoped in chat_active:
                raise Conflict("chat request is already in progress")
            cancel_event = Event()
            chat_active[scoped] = cancel_event
            return key, signature, cancel_event, None

    def _chat_finish(workflow_id: str, instance_id: str, key: str | None,
                     signature: str, result: dict[str, object] | None) -> None:
        if key is None:
            return
        scoped = f"{workflow_id}:{instance_id}:{key}"
        with chat_state_lock:
            chat_active.pop(scoped, None)
            if result is not None:
                chat_completed[scoped] = (signature, result)
                # Keep the registry bounded in the single-user desktop app.
                while len(chat_completed) > 512:
                    chat_completed.pop(next(iter(chat_completed)))

    def _chat_cancel(workflow_id: str, instance_id: str, request_id: str) -> bool:
        with chat_state_lock:
            event = chat_active.get(f"{workflow_id}:{instance_id}:{request_id}")
            if event is None:
                return False
            event.set()
            return True

    def _sse(event: str, data: object) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def _stream_chat(workflow_id: str, instance_id: str, body: ChatInput) -> StreamingResponse:
        if not llm.status()["configured"]:
            raise LLMUnavailable(str(llm.status().get("reason") or "AI provider is not configured"))
        key, signature, cancel_event, cached = _chat_begin(workflow_id, instance_id, body)

        def events():
            if cached is not None:
                yield _sse("message.started", {"requestId": key, "replayed": True})
                yield _sse("message.completed", cached)
                return
            assert cancel_event is not None or key is None
            event = cancel_event or Event()
            user_message = None
            try:
                user_message = graph_store.append_message(
                    workflow_id, instance_id, role="user", content=body.content
                )
                context = route_context(
                    workflow_id, instance_id,
                    graph_store.list_messages(workflow_id, instance_id)["messages"],
                )
                yield _sse("message.started", {
                    "requestId": key, "userMessage": user_message,
                })
                chunks = llm.stream(context, event) if hasattr(llm, "stream") else iter([llm.complete(context)])
                parts: list[str] = []
                for chunk in chunks:
                    if event.is_set():
                        yield _sse("message.cancelled", {"requestId": key})
                        return
                    if not isinstance(chunk, str) or not chunk:
                        continue
                    parts.append(chunk)
                    yield _sse("message.delta", {"requestId": key, "delta": chunk})
                if event.is_set():
                    yield _sse("message.cancelled", {"requestId": key})
                    return
                answer = "".join(parts).strip()
                if not answer:
                    raise LLMUnavailable("AI provider returned an empty response", code="aiEmptyResponse", status_code=502)
                assistant_message = graph_store.append_message(
                    workflow_id, instance_id, role="assistant", content=answer
                )
                result = {"userMessage": user_message, "assistantMessage": assistant_message}
                _chat_finish(workflow_id, instance_id, key, signature, result)
                yield _sse("message.completed", result)
            except LLMUnavailable as exc:
                _chat_finish(workflow_id, instance_id, key, signature, None)
                yield _sse("message.failed", {"requestId": key, "code": exc.code, "error": str(exc)})
            except Exception:
                _chat_finish(workflow_id, instance_id, key, signature, None)
                yield _sse("message.failed", {"requestId": key, "code": "aiUnavailable", "error": "AI provider is unavailable"})
            finally:
                # A client disconnect can close the generator before the
                # provider yields another token.  Always release the active
                # request slot; an already-completed replay remains cached.
                _chat_finish(workflow_id, instance_id, key, signature, None)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/chat")
    def chat(request: Request, workflow_id: str, instance_id: str, body: ChatInput):
        body = _body_with_header_key(request, body)
        if "text/event-stream" in request.headers.get("accept", ""):
            return _stream_chat(workflow_id, instance_id, body)
        if not llm.status()["configured"]:
            raise LLMUnavailable(str(llm.status().get("reason") or "AI provider is not configured"))
        key, signature, _, cached = _chat_begin(workflow_id, instance_id, body)
        if cached is not None:
            return cached
        try:
            user_message = graph_store.append_message(workflow_id, instance_id, role="user", content=body.content)
            context = route_context(workflow_id, instance_id, graph_store.list_messages(workflow_id, instance_id)["messages"])
            assistant_text = llm.complete(context)
            assistant_message = graph_store.append_message(workflow_id, instance_id, role="assistant", content=assistant_text)
            result = {"userMessage": user_message, "assistantMessage": assistant_message}
            _chat_finish(workflow_id, instance_id, key, signature, result)
            return result
        except Exception:
            _chat_finish(workflow_id, instance_id, key, signature, None)
            raise

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/chat/stream")
    def chat_stream(request: Request, workflow_id: str, instance_id: str, body: ChatInput):
        return _stream_chat(workflow_id, instance_id, _body_with_header_key(request, body))

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/chat/{request_id}/cancel")
    def cancel_chat(workflow_id: str, instance_id: str, request_id: str):
        return {"ok": True, "requestId": request_id, "cancelled": _chat_cancel(workflow_id, instance_id, request_id)}

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/messages/{message_id}/regenerate")
    def regenerate_message(workflow_id: str, instance_id: str, message_id: int,
                           body: RegenerateMessageInput):
        if not llm.status()["configured"]:
            return graph_store.commit_latest_local_user_edit(
                workflow_id, instance_id, message_id, content=body.content,
                expected_content_revision=body.expected_revision,
            )
        prepared = graph_store.prepare_latest_local_user_edit(
            workflow_id, instance_id, message_id, content=body.content,
            expected_content_revision=body.expected_revision,
        )
        assistant_text = llm.complete(route_context(workflow_id, instance_id, prepared["messages"]))
        return graph_store.commit_latest_local_user_edit(
            workflow_id, instance_id, message_id, content=body.content,
            expected_content_revision=body.expected_revision,
            assistant_content=assistant_text,
        )

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/runs", status_code=201)
    def create_agent_run(workflow_id: str, instance_id: str, body: CreateAgentRunInput):
        if run_service is None:
            raise LLMUnavailable("AI provider is not configured")
        return run_service.execute(workflow_id, instance_id, body.model_dump(by_alias=True))

    @app.get(prefix + "/workflows/{workflow_id}/instances/{instance_id}/runs")
    def list_agent_runs(workflow_id: str, instance_id: str):
        graph_store.list_messages(workflow_id, instance_id, scope="local")
        return {"runs": run_repository.list(workflow_id, instance_id)}

    @app.get(prefix + "/runs/{run_id}")
    def get_agent_run(run_id: str):
        return run_repository.get(run_id)

    @app.get(prefix + "/runs/{run_id}/events")
    def get_agent_run_events(run_id: str, after_sequence: int = Query(0, alias="afterSequence", ge=0),
                             limit: int = Query(100, ge=1, le=500)):
        return run_repository.events(run_id, after_sequence, limit)

    @app.get(prefix + "/workflows/{workflow_id}/artifacts")
    def list_artifacts(workflow_id: str):
        return {"artifacts": engineering.list_artifacts(workflow_id)}

    @app.post(prefix + "/workflows/{workflow_id}/artifacts", status_code=201)
    def create_artifact(workflow_id: str, body: ArtifactInput):
        return engineering.create_artifact(workflow_id, **body.model_dump(by_alias=False))

    @app.get(prefix + "/workflows/{workflow_id}/artifacts/{artifact_id}")
    def get_artifact(workflow_id: str, artifact_id: str):
        return engineering.get_artifact(workflow_id, artifact_id)

    @app.post(prefix + "/workflows/{workflow_id}/comparisons")
    def compare_branches(workflow_id: str, body: ComparisonInput):
        return engineering.compare(workflow_id, body.instance_ids)

    @app.post(prefix + "/workflows/{workflow_id}/knowledge-merges", status_code=201)
    def merge_knowledge(workflow_id: str, body: KnowledgeMergeInput):
        value = body.model_dump(by_alias=False)
        value["items"] = [item.model_dump(by_alias=True) for item in body.items]
        return engineering.merge_knowledge(workflow_id, **value)

    @app.get(prefix + "/workflows/{workflow_id}/instances/{instance_id}/knowledge")
    def accepted_knowledge(workflow_id: str, instance_id: str):
        return {"knowledgeItems": engineering.accepted_knowledge(workflow_id, instance_id)}

    @app.get(prefix + "/workflows/{workflow_id}/datasets")
    def list_datasets(workflow_id: str):
        return {"datasets": engineering.list_datasets(workflow_id)}

    @app.post(prefix + "/workflows/{workflow_id}/datasets", status_code=201)
    def create_dataset(workflow_id: str, body: DatasetInput):
        return engineering.create_dataset(workflow_id, name=body.name,
                                          description=body.description,
                                          cases=[case.model_dump() for case in body.cases])

    @app.get(prefix + "/workflows/{workflow_id}/experiments")
    def list_experiments(workflow_id: str):
        return {"experiments": engineering.list_experiments(workflow_id)}

    @app.post(prefix + "/workflows/{workflow_id}/experiments", status_code=201)
    def create_experiment(workflow_id: str, body: ExperimentInput):
        return engineering.create_experiment(workflow_id, **body.model_dump(by_alias=False))

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/fork", status_code=201)
    def fork(workflow_id: str, instance_id: str, body: ForkInput):
        return graph_store.fork(workflow_id, instance_id, **body.model_dump(by_alias=False))

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/fork-chat", status_code=201)
    def fork_chat(workflow_id: str, instance_id: str, body: ForkChatInput):
        result = graph_store.fork(
            workflow_id, instance_id, **body.model_dump(by_alias=False), surface_scope="turn"
        )
        child_id = result["node"]["id"]
        local = graph_store.list_messages(workflow_id, child_id, scope="local")["messages"]
        existing_answer = next((message for message in reversed(local)
                                if message["role"] == "assistant"), None)
        reply_status, reply_error = "recorded", None
        assistant_message = existing_answer
        if existing_answer is not None:
            reply_status = "completed"
        elif body.initial_message and body.initial_message.strip() and llm.status()["configured"]:
            try:
                context = route_context(
                    workflow_id, child_id,
                    graph_store.list_messages(workflow_id, child_id, scope="effective")["messages"],
                )
                answer = llm.complete(context)
                assistant_message = graph_store.append_message(
                    workflow_id, child_id, role="assistant", content=answer
                )
                reply_status = "completed"
            except LLMUnavailable as exc:
                reply_status, reply_error = "failed", exc.code
            except Exception:
                reply_status, reply_error = "failed", "aiUnavailable"
        graph = graph_store.get_graph(workflow_id)
        return {"node": result["node"], "graphRevision": graph["graphRevision"],
                "replyStatus": reply_status, "replyErrorCode": reply_error,
                "assistantMessage": assistant_message}

    @app.patch(prefix + "/workflows/{workflow_id}/instances/{instance_id}")
    def rename_instance(workflow_id: str, instance_id: str, body: RenameInstanceInput):
        return graph_store.rename_instance(
            workflow_id, instance_id, title=body.title,
            expected_revision=body.expected_revision,
        )

    @app.patch(prefix + "/workflows/{workflow_id}")
    def rename_workflow(workflow_id: str, body: RenameWorkflowInput):
        return graph_store.rename_workflow(
            workflow_id, name=body.name, expected_revision=body.expected_revision,
        )

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/activate")
    def activate(workflow_id: str, instance_id: str, body: ActivateInput):
        del body
        return graph_store.activate(workflow_id, instance_id)

    @app.get(prefix + "/workflows/{workflow_id}/topics/{topic_id}/routes")
    def routes(workflow_id: str, topic_id: str, include_pruned: bool = Query(False, alias="includePruned")):
        return graph_store.topic_routes(workflow_id, topic_id, include_pruned)

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/prune-plan")
    def prune_plan(workflow_id: str, instance_id: str, body: PrunePlanInput):
        return graph_store.prune_plan(workflow_id, instance_id, allow_root=body.allow_root)

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/prune-commit")
    def prune_commit(workflow_id: str, instance_id: str, body: PruneCommitInput):
        return graph_store.prune_commit(workflow_id, instance_id,
                                        expected_revision=body.expected_revision,
                                        idempotency_key=body.idempotency_key,
                                        allow_root=body.allow_root)

    return app
