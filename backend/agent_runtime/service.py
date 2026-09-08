from __future__ import annotations

import json
import math
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from agent_runtime.adapters import AgentModelPort
from agent_runtime.prompt_builder import assemble_agent_context
from agent_runtime.repository import AgentRunRepository
from agent_runtime.tools import ToolRegistry
from api.llm import LLMUnavailable
from graph_core import Conflict, GraphStore, Validation
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
    "networkMode": 20,
}

_PROVIDER_ERRORS = {
    "aiTimeout": ("Agent provider request timed out", 504),
    "aiConnectionFailed": ("Agent provider connection failed after automatic retries", 503),
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
        if key == "networkMode" and item not in {"auto", "system", "direct"}:
            raise AgentRunError("modelProtocolError", "Model snapshot is invalid", 502)
        result[key] = item
    timeout = value.get("connectTimeoutSeconds")
    if timeout is not None:
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or not 1 <= timeout <= 60):
            raise AgentRunError("modelProtocolError", "Model snapshot is invalid", 502)
        result["connectTimeoutSeconds"] = float(timeout)
    response_timeout = value.get("responseTimeout")
    if response_timeout == "none":
        result["responseTimeout"] = "none"
    retries = value.get("connectionRetryAttempts")
    if retries is not None:
        if not isinstance(retries, int) or isinstance(retries, bool) or not 1 <= retries <= 10:
            raise AgentRunError("modelProtocolError", "Model snapshot is invalid", 502)
        result["connectionRetryAttempts"] = retries
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

    def enqueue(self, workflow_id: str, instance_id: str, request: dict[str, Any],
                submit: Callable[[str], None]) -> dict[str, Any]:
        """Freeze and persist a run, then hand only its durable id to a worker."""
        claim_key = (workflow_id, instance_id, request["idempotencyKey"])
        with self._claim(claim_key):
            return self._execute_claimed(
                workflow_id, instance_id, request, background_submit=submit,
            )

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self.repository.request_cancel(run_id)

    def retry(self, run_id: str, options: dict[str, Any] | None = None,
              submit: Callable[[str], None] | None = None) -> dict[str, Any]:
        source = self.repository.get(run_id, details=False)
        if source["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            raise AgentRunError("runNotRetryable", "Agent run is not in a retryable state", 409, run_id)
        request = self.repository.retry_payload(run_id)
        options = options or {}
        request["idempotencyKey"] = options.get("idempotencyKey") or ("retry-" + uuid.uuid4().hex)
        if options.get("expectedContentRevision") is None:
            snapshot = self.graph.list_messages(source["workflowId"], source["instanceId"], scope="effective")
            request["expectedContentRevision"] = snapshot["contentRevision"]
        else:
            request["expectedContentRevision"] = options["expectedContentRevision"]
        key = (source["workflowId"], source["instanceId"], request["idempotencyKey"])
        with self._claim(key):
            return self._execute_claimed(source["workflowId"], source["instanceId"], request,
                                         parent_run_id=run_id, background_submit=submit)

    def resume_queued(self, run_id: str) -> dict[str, Any]:
        """Execute one durable queued run after verifying its frozen inputs.

        Queued runs are safe to resume after a process restart because no model
        or tool step has started yet. Running calls remain interrupt-only: their
        upstream completion and side-effect boundary cannot be inferred safely.
        """
        payload = self.repository.execution_payload(run_id)
        if payload["status"] != "queued":
            return self.repository.get(run_id)
        context = payload["context"]
        request = payload["request"]
        try:
            bound_model = self.model.bind()
            current_snapshot = _safe_model_snapshot(bound_model.snapshot())
            if current_snapshot != payload["modelSnapshot"]:
                raise AgentRunError(
                    "modelConfigurationChanged",
                    "Model configuration changed before the queued run started", 409, run_id,
                )
            assembled = assemble_agent_context(
                route_messages=context["messages"],
                accepted_knowledge=context.get("acceptedKnowledge", []),
                request=request,
                tools=context["availableTools"],
                provider_system_prompt=current_snapshot.get("systemPrompt", ""),
            )
            if (assembled.prompt_layout_version != context.get("promptLayoutVersion")
                    or assembled.stable_prefix_sha256 != context.get("stablePrefixSha256")
                    or assembled.request_sha256 != context.get("modelRequestSha256")):
                raise AgentRunError(
                    "runContextIntegrityFailed", "Queued run context could not be verified",
                    409, run_id,
                )
            return self._run_created(
                run_id, bound_model, list(assembled.messages), assembled.tools,
            )
        except AgentRunError as exc:
            self.repository.fail(run_id, exc.code)
            raise
        except LLMUnavailable as exc:
            failure = _stable_provider_error(exc, run_id)
            self.repository.fail(run_id, failure.code)
            raise failure from exc
        except Exception as exc:
            self.repository.fail(run_id, "aiUnavailable")
            raise AgentRunError("aiUnavailable", "Agent provider is unavailable", 503, run_id) from exc

    def decide_approval(self, run_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        approval_key = ("approval", run_id, approval_id)
        with self._claim(approval_key):
            try:
                decided = self.repository.decide_approval(run_id, approval_id, decision)
            except Conflict as exc:
                raise AgentRunError("approvalConflict", str(exc), 409, run_id) from exc
            current = self.repository.get(run_id)
            approval = next(
                (item for item in current["approvalRequests"]
                 if item["approvalId"] == approval_id), None
            )
            if approval is None:
                raise AgentRunError("notFound", "Approval request was not found", 404, run_id)
            if decision == "rejected" or current["status"] in {
                "completed", "failed", "cancelled", "interrupted"
            }:
                return current
            cancelled = self._finish_cancel_if_requested(run_id)
            if cancelled is not None:
                return cancelled
            matching_call = next(
                (call for call in current.get("toolCalls", [])
                 if call["toolCallId"] == approval["toolCallId"]), None
            )
            if matching_call is None:
                cancelled = self._fail_or_finish_cancel(run_id, "toolExecutionFailed")
                if cancelled is not None:
                    return cancelled
                raise AgentRunError("toolExecutionFailed", "Approved tool call is missing", 500, run_id)
            if matching_call["status"] == "completed":
                return self.repository.get(run_id)
            arguments = approval["arguments"]
            try:
                self.repository.claim_approved_tool(run_id, approval_id)
            except Conflict as exc:
                cancelled = self._finish_cancel_if_requested(run_id)
                if cancelled is not None:
                    return cancelled
                raise AgentRunError(
                    "approvalConflict", "Approved tool call could not be claimed", 409, run_id
                ) from exc
            tool = self.tools.resolve(approval["toolName"])
            # Approval is necessary but not sufficient authority. Revalidate
            # the exact persisted tool contract against the small executor
            # allowlist *before* invoking any implementation. Future tools
            # cannot become executable merely by declaring a side effect.
            if (tool is None or tool.name != "propose_patch" or tool.version != "1.0.0"
                    or tool.side_effect != "artifact"
                    or approval["toolVersion"] != tool.version
                    or approval["sideEffect"] != tool.side_effect
                    or self.engineering is None):
                self.repository.finish_approved_tool(
                    run_id, approval_id, output=None, error_code="unknownTool", duration_ms=0,
                )
                cancelled = self._fail_or_finish_cancel(run_id, "unknownTool")
                if cancelled is not None:
                    return cancelled
                raise AgentRunError(
                    "unknownTool", "Approved tool has no allowed side-effect executor", 409, run_id
                )
            started = time.monotonic()
            try:
                self.tools.validate(tool, arguments)
                output = tool.execute(arguments)
                artifact = self.engineering.create_artifact(
                    current["workflowId"], name=f"{output['path']}.patch", kind="patch",
                    mime_type="text/x-diff", content=output["patch"],
                    instance_id=current["instanceId"], run_id=run_id,
                    metadata={"proposedPath": output["path"], "filesystemChanged": False},
                )
                output = {**output, "artifactId": artifact["artifactId"]}
                self.repository.finish_approved_tool(
                    run_id, approval_id, output=output, error_code=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                cancelled = self._finish_cancel_if_requested(run_id)
                if cancelled is not None:
                    return cancelled
                try:
                    self.repository.complete(
                        run_id,
                        f"Patch proposal for {output['path']} was saved as a reviewable Artifact; "
                        "no workspace file was changed.",
                    )
                    return self.repository.get(run_id)
                except Conflict as exc:
                    current = self.repository.get(run_id, details=False)
                    if current["status"] == "cancelled":
                        return self.repository.get(run_id)
                    if current["status"] == "cancelling":
                        return self.repository.finish_cancel(run_id)
                    cancelled = self._fail_or_finish_cancel(run_id, "runRevisionConflict")
                    if cancelled is not None:
                        return cancelled
                    raise AgentRunError(
                        "runRevisionConflict", "Route changed before approval completed", 409, run_id
                    ) from exc
            except (Conflict, AgentRunError):
                raise
            except Exception as exc:
                self.repository.finish_approved_tool(
                    run_id, approval_id, output=None, error_code="toolExecutionFailed",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                cancelled = self._fail_or_finish_cancel(run_id, "toolExecutionFailed")
                if cancelled is not None:
                    return cancelled
                raise AgentRunError("toolExecutionFailed", "Approved tool execution failed", 500,
                                    run_id) from exc

    def _finish_cancel_if_requested(self, run_id: str) -> dict[str, Any] | None:
        status = self.repository.status(run_id)
        if status == "cancelled":
            return self.repository.get(run_id)
        if status == "cancelling":
            return self.repository.finish_cancel(run_id)
        return None

    def _fail_or_finish_cancel(self, run_id: str, code: str) -> dict[str, Any] | None:
        """Let exactly one terminal transition win a fail/cancel race."""
        self.repository.fail(run_id, code)
        return self._finish_cancel_if_requested(run_id)

    def _execute_claimed(self, workflow_id: str, instance_id: str,
                         request: dict[str, Any], parent_run_id: str | None = None,
                         background_submit: Callable[[str], None] | None = None) -> dict[str, Any]:
        try:
            existing = self.repository.find_idempotent(workflow_id, instance_id, request)
        except Conflict as exc:
            raise AgentRunError("idempotencyConflict", str(exc), 409) from exc
        if existing is not None:
            if background_submit is not None:
                return existing
            if existing["status"] == "awaiting_approval":
                return existing
            if existing["status"] in {"queued", "running", "cancelling"}:
                return self.repository.wait_terminal(existing["runId"])
            return existing
        # Capture the live route, every route-node revision, and its effective
        # messages under GraphStore's single lock.  Using separate graph and
        # message reads here would leave a race where a parent could change
        # between the two snapshots and the run would guard the wrong input.
        # The runtime used to accept unbounded effective messages, so keep the
        # preview limit effectively unbounded rather than silently truncating
        # the model context at the HTTP preview default.
        try:
            snapshot = self.graph.context_preview(
                workflow_id, instance_id, max_chars=2_147_483_647
            )
        except Validation as exc:
            # Preserve the Runtime API's stable inactive-target contract; the
            # graph projection intentionally reports this as a validation
            # error for its own callers.
            raise AgentRunError(
                "runTargetInactive", "Conversation instance is not active", 409
            ) from exc
        if snapshot["contentRevision"] != request["expectedContentRevision"]:
            raise AgentRunError("runRevisionConflict", "Route changed before the run started", 409)
        memory_route = snapshot["memoryRoute"]
        route_revision_vector = [
            {
                "instanceId": node["instanceId"],
                "contentRevision": node["contentRevision"],
            }
            for node in memory_route
        ]
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
        assembled = assemble_agent_context(
            route_messages=snapshot["messages"], accepted_knowledge=accepted_knowledge,
            request=request, tools=self.tools.specs(),
            provider_system_prompt=model_snapshot.get("systemPrompt", ""),
        )
        tool_specs, prompt_messages = assembled.tools, assembled.messages
        context = {"workflowId": workflow_id, "instanceId": instance_id,
                   "inputContentRevision": snapshot["contentRevision"],
                   "memoryRoute": memory_route,
                   "routeRevisionVector": route_revision_vector,
                   "acceptedKnowledge": accepted_knowledge,
                   "availableTools": tool_specs,
                   "promptLayoutVersion": assembled.prompt_layout_version,
                   "stablePrefixSha256": assembled.stable_prefix_sha256,
                   "modelRequestSha256": assembled.request_sha256,
                   "messages": snapshot["messages"], "objective": request["objective"],
                   "constraints": request["constraints"], "deliverables": request["deliverables"],
                   "acceptanceChecks": request["acceptanceChecks"]}
        try:
            run, created = self.repository.create(workflow_id=workflow_id, instance_id=instance_id,
                                                   request=request, context=context,
                                                   model_snapshot=model_snapshot,
                                                   parent_run_id=parent_run_id)
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
            if background_submit is not None:
                return run
            return self.repository.wait_terminal(run["runId"])
        run_id = run["runId"]
        if background_submit is not None:
            background_submit(run_id)
            return self.repository.get(run_id)
        return self._run_created(run_id, bound_model, list(prompt_messages), tool_specs)

    def _run_created(self, run_id: str, bound_model: Any,
                     prompt_messages: list[dict[str, Any]],
                     tool_specs: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            self.repository.start(run_id)
            messages = list(prompt_messages)
            step_sequence = 0
            for _ in range(self.max_steps):
                cancelled = self._finish_cancel_if_requested(run_id)
                if cancelled is not None:
                    return cancelled
                step_sequence += 1
                self.repository.event(run_id, "model.started", {"stepSequence": step_sequence})
                turn = None
                try:
                    turn = bound_model.next(messages, tool_specs)
                    self._validate_turn(turn)
                except LLMUnavailable as exc:
                    failure = _stable_provider_error(exc, run_id)
                    self.repository.record_model(
                        run_id, step_sequence, "providerError", error_code=failure.code
                    )
                    raise failure from exc
                except AgentRunError as exc:
                    self.repository.record_model(
                        run_id, step_sequence, "invalidTurn",
                        getattr(turn, "usage", None), error_code=exc.code,
                    )
                    raise
                except ValueError as exc:
                    self.repository.record_model(
                        run_id, step_sequence, "protocolError", getattr(exc, "usage", None),
                        error_code="modelProtocolError",
                    )
                    raise AgentRunError("modelProtocolError", "Model returned an invalid agent turn", 502) from exc
                turn_kind = "finalAnswer" if turn.final_answer is not None else "toolRequest"
                self.repository.record_model(
                    run_id, step_sequence, turn_kind, getattr(turn, "usage", None)
                )
                cancelled = self._finish_cancel_if_requested(run_id)
                if cancelled is not None:
                    return cancelled
                if turn.final_answer is not None:
                    try:
                        return self.repository.complete(run_id, turn.final_answer.strip())
                    except Conflict as exc:
                        cancelled = self._finish_cancel_if_requested(run_id)
                        if cancelled is not None:
                            return cancelled
                        raise AgentRunError("runRevisionConflict", "Route changed while the run was executing", 409) from exc
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
                        duration_ms=duration, provider_call_id=turn.tool_call_id)
                    raise AgentRunError("toolArgumentsInvalid", "Tool arguments are invalid") from exc
                approval_id: str | None = None
                if tool.side_effect != "none":
                    step_sequence += 1
                    return self.repository.request_approval(
                        run_id, step_sequence, name=tool.name, version=tool.version,
                        arguments=turn.tool_arguments, side_effect=tool.side_effect,
                        provider_call_id=turn.tool_call_id,
                    )
                tool_sequence = step_sequence + 1
                self.repository.event(run_id, "tool.started", {
                    "toolName": tool.name, "toolVersion": tool.version,
                    "stepSequence": tool_sequence,
                })
                try:
                    output = tool.execute(turn.tool_arguments)
                except Exception as exc:
                    duration = int((time.monotonic() - started) * 1000)
                    step_sequence += 1
                    self.repository.record_tool(run_id, step_sequence, name=tool.name, version=tool.version,
                        arguments=turn.tool_arguments, output=None, error_code="toolExecutionFailed",
                        duration_ms=duration, provider_call_id=turn.tool_call_id)
                    raise AgentRunError("toolExecutionFailed", "Tool execution failed", 500) from exc
                duration = int((time.monotonic() - started) * 1000)
                step_sequence += 1
                _, call_id = self.repository.record_tool(run_id, step_sequence, name=tool.name,
                    version=tool.version, arguments=turn.tool_arguments, output=output,
                    error_code=None, duration_ms=duration, provider_call_id=turn.tool_call_id)
                messages.append({"role": "assistant", "content": "", "toolCall": {
                    "id": turn.tool_call_id or call_id, "name": tool.name, "arguments": turn.tool_arguments}})
                messages.append({"role": "tool", "content": json.dumps(
                                    output, ensure_ascii=False, allow_nan=False),
                                 "toolCallId": turn.tool_call_id or call_id})
            raise AgentRunError("modelProtocolError", "Agent run exceeded the maximum step count", 502)
        except AgentRunError as exc:
            cancelled = self._fail_or_finish_cancel(run_id, exc.code)
            if cancelled is not None:
                return cancelled
            exc.run_id = run_id
            raise
        except LLMUnavailable as exc:
            failure = _stable_provider_error(exc, run_id)
            cancelled = self._fail_or_finish_cancel(run_id, failure.code)
            if cancelled is not None:
                return cancelled
            raise failure from exc
        except Conflict as exc:
            current = self.repository.get(run_id, details=False)
            if current["status"] == "cancelled":
                return self.repository.get(run_id)
            if current["status"] == "cancelling":
                return self.repository.finish_cancel(run_id)
            code = current.get("errorCode") or "runRevisionConflict"
            self.repository.fail(run_id, code)
            raise AgentRunError(code, "Agent run is no longer active", 409, run_id) from exc
        except Exception as exc:
            cancelled = self._fail_or_finish_cancel(run_id, "aiUnavailable")
            if cancelled is not None:
                return cancelled
            raise AgentRunError("aiUnavailable", "Agent run failed", 503, run_id) from exc
