from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from api.llm import LLMClient, LLMUnavailable
from api.model_settings import RuntimeModelSettings
from graph_core import Conflict, GraphStore, NotFound, Validation


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


def open_default_store() -> GraphStore:
    try:
        return GraphStore(default_database_path())
    except PermissionError:
        # Sandboxed hosts may deny the platform data directory. Keep runtime
        # state outside the source tree while preserving a usable local demo.
        return GraphStore(Path(tempfile.gettempdir()) / "WeavePath" / "data" / "workspace.db")


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CreateWorkflow(CamelModel):
    name: str
    root_title: str = Field(alias="rootTitle")
    root_topic_id: str | None = Field(None, alias="rootTopicId")
    provider: str = "local"
    root_instance_id: str | None = Field(None, alias="rootInstanceId")
    provider_conversation_id: str | None = Field(None, alias="providerConversationId")


class MessageInput(CamelModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatInput(CamelModel):
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class RegenerateMessageInput(ChatInput):
    expected_revision: int = Field(alias="expectedRevision", ge=0)


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
    title: str
    topic_id: str | None = Field(None, alias="topicId")
    provider: str | None = None
    instance_id: str | None = Field(None, alias="instanceId")
    provider_conversation_id: str | None = Field(None, alias="providerConversationId")
    initial_message: str | None = Field(None, alias="initialMessage")


class ActivateInput(CamelModel):
    preference_key: str | None = Field(None, alias="preferenceKey")


class PrunePlanInput(CamelModel):
    allow_root: bool = Field(False, alias="allowRoot")


class PruneCommitInput(PrunePlanInput):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1)


def create_app(store: GraphStore | None = None, llm_client: LLMClient | None = None,
               model_settings: RuntimeModelSettings | None = None) -> FastAPI:
    owned = store is None
    graph_store = store or open_default_store()
    settings_path = (Path(graph_store.db_path).with_name("model-settings.json")
                     if graph_store.db_path != ":memory:"
                     else Path(default_database_path()).with_name("model-settings.json"))
    settings = model_settings or RuntimeModelSettings(settings_path)
    llm: LLMClient = llm_client or settings

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owned:
            graph_store.close()

    app = FastAPI(title="WeavePath API", version="0.1.0", lifespan=lifespan)
    app.state.store = graph_store
    app.state.model_settings = settings

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

    @app.get(prefix + "/health")
    def health():
        return {"ok": True, "service": "weavepath", "version": app.version,
                "schemaVersion": 1, "aiConfigured": bool(llm.status()["configured"])}

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

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/messages", status_code=201)
    def append(workflow_id: str, instance_id: str, body: MessageInput):
        return graph_store.append_message(workflow_id, instance_id, **body.model_dump())

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/chat")
    def chat(workflow_id: str, instance_id: str, body: ChatInput):
        if not llm.status()["configured"]:
            raise LLMUnavailable(str(llm.status().get("reason") or "AI provider is not configured"))
        user_message = graph_store.append_message(workflow_id, instance_id, role="user", content=body.content)
        context = graph_store.list_messages(workflow_id, instance_id)["messages"]
        assistant_text = llm.complete(context)
        assistant_message = graph_store.append_message(
            workflow_id, instance_id, role="assistant", content=assistant_text
        )
        return {"userMessage": user_message, "assistantMessage": assistant_message}

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
        assistant_text = llm.complete(prepared["messages"])
        return graph_store.commit_latest_local_user_edit(
            workflow_id, instance_id, message_id, content=body.content,
            expected_content_revision=body.expected_revision,
            assistant_content=assistant_text,
        )

    @app.post(prefix + "/workflows/{workflow_id}/instances/{instance_id}/fork", status_code=201)
    def fork(workflow_id: str, instance_id: str, body: ForkInput):
        return graph_store.fork(workflow_id, instance_id, **body.model_dump(by_alias=False))

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


app = create_app()
