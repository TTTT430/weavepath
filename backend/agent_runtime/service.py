from __future__ import annotations

import json
import math
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit

from agent_runtime.adapters import AgentModelPort
from agent_runtime.repository import AgentRunRepository
from agent_runtime.tools import ToolRegistry
from api.llm import LLMUnavailable
from graph_core import Conflict, GraphStore
from engineering import EngineeringRepository


class AgentRunError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422,
                 run_id: str | None = None) -> None:
        super().__init__(message)
        self.code, self.status_code, self.run_id = code, status_code, run_id


_MODEL_SNAPSHOT_FIELDS = {
    "provider": 200,
    "model": 200,
    "baseUrl": 2_048,
    "systemPrompt": 20_000,
    "adapterVersion": 200,
}

_PROVIDER_ERRORS = {
    "aiTimeout": ("Agent provider request timed out", 504),
    "aiEmptyResponse": ("Agent provider returned an empty response", 502),
    "aiUnavailable": ("Agent provider is unavailable", 503),
}


def _stable_provider_error(exc: LLMUnavailable, run_id: str | None = None) -> AgentRunError:
    code = exc.code if exc.code in _PROVIDER_ERRORS else "aiUnavailable"
    message, status = _PROVIDER_ERRORS[code]
    return AgentRunError(code, message, status, run_id)


def _safe_model_snapshot(value: Any) -> dict[str, Any]:
    """Persist only the documented, non-credential model metadata."""
    if not isinstance(value, dict):
        raise AgentRunError("modelProtocolError", "Model snapshot is invalid", 502)
    result: dict[str, Any] = {}
    for key, limit in _MODEL_SNAPSHOT_FIELDS.items():
        item = value.get(key)
        if item is None:
            continue
        if not isinstance(item, str) or len(item) > limit:
            raise AgentRunError("modelProtocolError", "Model snapshot is invalid", 502)
        if key == "baseUrl":
            parsed = urlsplit(item)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise AgentRunError("modelProtocolError", "Model snapshot is invalid", 502)
        result[key] = item
    timeout = value.get("timeoutSeconds")
    if timeout is not None:
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or not 1 <= timeout <= 300):
            raise AgentRunError("modelProtocolError", "Model snapshot is invalid", 502)
        result["timeoutSeconds"] = float(timeout)
    return result


class AgentRuntimeService:
    def __init__(self, graph: GraphStore, repository: AgentRunRepository,
                 model: AgentModelPort, tools: ToolRegistry, max_steps: int = 6,
                 engineering: EngineeringRepository | None = None) -> None:
        self.graph, self.repository, self.model, self.tools = graph, repository, model, tools
        self.max_steps = max_steps
        self.engineering = engineering
        self._claim_guard = threading.Lock()
        self._claim_locks: dict[tuple[str, str, str], tuple[threading.Lock, int]] = {}

    @contextmanager
    def _claim(self, key: tuple[str, str, str]) -> Iterator[None]:
        """Serialize one idempotency key through bind, claim, and execution.

        WeavePath v1 runs as one local server process. This prevents a same-key
        replay from binding a second provider snapshot before the first request
        has durably claimed its run.
        """
        with self._claim_guard:
            lock, users = self._claim_locks.get(key, (threading.Lock(), 0))
            self._claim_locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._claim_guard:
                current_lock, users = self._claim_locks[key]
                if users == 1:
                    del self._claim_locks[key]
                else:
                    self._claim_locks[key] = (current_lock, users - 1)

    def _route(self, workflow_id: str, instance_id: str) -> list[dict[str, str]]:
        graph = self.graph.get_graph(workflow_id)
        nodes = {node["id"]: node for node in graph["nodes"]}
        current = nodes.get(instance_id)
        if current is None:
            raise AgentRunError("notFound", "Conversation instance was not found", 404)
        if current["status"] != "active":
            raise AgentRunError("runTargetInactive", "Conversation instance is not active", 409)
        route: list[dict[str, str]] = []
        seen: set[str] = set()
        while current is not None:
            if current["id"] in seen:
                raise AgentRunError("modelProtocolError", "Conversation route is invalid", 500)
            seen.add(current["id"])
            route.append({"instanceId": current["id"], "topicId": current["topicId"],
                          "title": current["title"]})
            parent_id = current.get("parentId")
            current = nodes.get(parent_id) if parent_id else None
        route.reverse()
        return route

    @staticmethod
    def _validate_turn(turn: Any) -> None:
        final = getattr(turn, "final_answer", None)
        tool_name = getattr(turn, "tool_name", None)
        arguments = getattr(turn, "tool_arguments", None)
        tool_call_id = getattr(turn, "tool_call_id", None)
        has_final = final is not None
        has_tool = tool_name is not None or arguments is not None or tool_call_id is not None
        if has_final == has_tool:
            raise AgentRunError("modelProtocolError", "Model returned an ambiguous agent turn", 502)
        if has_final and (not isinstance(final, str) or not final.strip()):
            raise AgentRunError("modelProtocolError", "Model returned an empty final answer", 502)
        if has_tool and (not isinstance(tool_name, str) or not tool_name.strip()
                         or not isinstance(arguments, dict)):
            raise AgentRunError("modelProtocolError", "Model returned an invalid tool request", 502)

    def execute(self, workflow_id: str, instance_id: str, request: dict[str, Any]) -> dict[str, Any]:
        claim_key = (workflow_id, instance_id, request["idempotencyKey"])
        with self._claim(claim_key):
            return self._execute_claimed(workflow_id, instance_id, request)

    def _execute_claimed(self, workflow_id: str, instance_id: str,
                         request: dict[str, Any]) -> dict[str, Any]:
        try:
            existing = self.repository.find_idempotent(workflow_id, instance_id, request)
        except Conflict as exc:
            raise AgentRunError("idempotencyConflict", str(exc), 409) from exc
        if existing is not None:
            if existing["status"] in {"queued", "running"}:
                return self.repository.wait_terminal(existing["runId"])
            return existing
        snapshot = self.graph.list_messages(workflow_id, instance_id, scope="effective")
        if snapshot["contentRevision"] != request["expectedContentRevision"]:
            raise AgentRunError("runRevisionConflict", "Route changed before the run started", 409)
        memory_route = self._route(workflow_id, instance_id)
        accepted_knowledge = (self.engineering.accepted_knowledge(workflow_id, instance_id)
                              if self.engineering else [])
        try:
            bound_model = self.model.bind()
            model_snapshot = _safe_model_snapshot(bound_model.snapshot())
        except AgentRunError:
            raise
        except LLMUnavailable as exc:
            raise _stable_provider_error(exc) from exc
        except Exception as exc:
            raise AgentRunError(
                "aiUnavailable", "Agent provider is unavailable", 503
            ) from exc
        tool_specs = self.tools.specs()
        context = {"workflowId": workflow_id, "instanceId": instance_id,
                   "inputContentRevision": snapshot["contentRevision"],
                   "memoryRoute": memory_route,
                   "acceptedKnowledge": accepted_knowledge,
                   "availableTools": tool_specs,
                   "messages": snapshot["messages"], "objective": request["objective"],
                   "constraints": request["constraints"], "deliverables": request["deliverables"],
                   "acceptanceChecks": request["acceptanceChecks"]}
        try:
            run, created = self.repository.create(workflow_id=workflow_id, instance_id=instance_id,
                                                   request=request, context=context,
                                                   model_snapshot=model_snapshot)
        except Conflict as exc:
            reason = str(exc).lower()
            if "idempotency" in reason:
                code = "idempotencyConflict"
            elif "not active" in reason:
                code = "runTargetInactive"
            else:
                code = "runRevisionConflict"
            raise AgentRunError(code, str(exc), 409) from exc
        if not created:
            return self.repository.wait_terminal(run["runId"])
        run_id = run["runId"]
        try:
            self.repository.start(run_id)
            messages = [{"role": m["role"], "content": m["content"]} for m in snapshot["messages"]
                        if m["role"] in {"system", "user", "assistant"}]
            messages.append({"role": "user", "content": json.dumps({
                "objective": request["objective"], "constraints": request["constraints"],
                "deliverables": request["deliverables"],
                "acceptanceChecks": request["acceptanceChecks"],
                "acceptedKnowledge": accepted_knowledge,
            }, ensure_ascii=False)})
            step_sequence = 0
            for _ in range(self.max_steps):
                step_sequence += 1
                self.repository.event(run_id, "model.started", {"stepSequence": step_sequence})
                try:
                    turn = bound_model.next(messages, tool_specs)
                    self._validate_turn(turn)
                except LLMUnavailable as exc:
                    failure = _stable_provider_error(exc, run_id)
                    self.repository.event(run_id, "model.failed", {"errorCode": failure.code})
                    raise failure from exc
                except AgentRunError as exc:
                    self.repository.event(run_id, "model.failed", {"errorCode": exc.code})
                    raise
                except ValueError as exc:
                    self.repository.event(run_id, "model.failed", {"errorCode": "modelProtocolError"})
                    raise AgentRunError("modelProtocolError", "Model returned an invalid agent turn", 502) from exc
                if turn.final_answer is not None:
                    self.repository.record_model(run_id, step_sequence, "finalAnswer")
                    try:
                        return self.repository.complete(run_id, turn.final_answer.strip())
                    except Conflict as exc:
                        raise AgentRunError("runRevisionConflict", "Route changed while the run was executing", 409) from exc
                self.repository.record_model(run_id, step_sequence, "toolRequest")
                self.repository.event(run_id, "tool.requested", {
                    "toolName": turn.tool_name, "stepSequence": step_sequence,
                })
                tool = self.tools.resolve(turn.tool_name)
                if not tool:
                    raise AgentRunError("unknownTool", "Model requested an unknown tool")
                started = time.monotonic()
                try:
                    self.tools.validate(tool, turn.tool_arguments)
                except ValueError as exc:
                    step_sequence += 1
                    duration = int((time.monotonic() - started) * 1000)
                    self.repository.record_tool(run_id, step_sequence, name=tool.name, version=tool.version,
                        arguments=turn.tool_arguments, output=None, error_code="toolArgumentsInvalid",
                        duration_ms=duration)
                    raise AgentRunError("toolArgumentsInvalid", "Tool arguments are invalid") from exc
                self.repository.event(run_id, "tool.started", {
                    "toolName": tool.name, "toolVersion": tool.version,
                    "stepSequence": step_sequence + 1,
                })
                try:
                    output = tool.execute(turn.tool_arguments)
                except Exception as exc:
                    step_sequence += 1
                    duration = int((time.monotonic() - started) * 1000)
                    self.repository.record_tool(run_id, step_sequence, name=tool.name, version=tool.version,
                        arguments=turn.tool_arguments, output=None, error_code="toolExecutionFailed",
                        duration_ms=duration)
                    raise AgentRunError("toolExecutionFailed", "Tool execution failed", 500) from exc
                duration = int((time.monotonic() - started) * 1000)
                step_sequence += 1
                _, call_id = self.repository.record_tool(run_id, step_sequence, name=tool.name,
                    version=tool.version, arguments=turn.tool_arguments, output=output,
                    error_code=None, duration_ms=duration)
                messages.append({"role": "assistant", "content": "", "toolCall": {
                    "id": turn.tool_call_id or call_id, "name": tool.name, "arguments": turn.tool_arguments}})
                messages.append({"role": "tool", "content": json.dumps(
                                    output, ensure_ascii=False, allow_nan=False),
                                 "toolCallId": turn.tool_call_id or call_id})
            raise AgentRunError("modelProtocolError", "Agent run exceeded the maximum step count", 502)
        except AgentRunError as exc:
            self.repository.fail(run_id, exc.code)
            exc.run_id = run_id
            raise
        except LLMUnavailable as exc:
            failure = _stable_provider_error(exc, run_id)
            self.repository.fail(run_id, failure.code)
            raise failure from exc
        except Conflict as exc:
            current = self.repository.get(run_id, details=False)
            code = current.get("errorCode") or "runRevisionConflict"
            self.repository.fail(run_id, code)
            raise AgentRunError(code, "Agent run is no longer active", 409, run_id) from exc
        except Exception as exc:
            self.repository.fail(run_id, "aiUnavailable")
            raise AgentRunError("aiUnavailable", "Agent run failed", 503, run_id) from exc
