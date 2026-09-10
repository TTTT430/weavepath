"""Capability-aware adapter for Codex/Claude companion bridges.

The core never imports a provider SDK or assumes two hosts expose identical
conversation semantics. A companion supplies negotiated capabilities and a
small canonical transport; this adapter validates every response before it can
become a workflow binding.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from host_adapters.ports import (
    HostAdapterError,
    HostBinding,
    HostCapabilities,
    HostCapabilityUnsupported,
    HostContext,
    HostDescriptor,
    HostResult,
    Page,
)


@runtime_checkable
class HostBridgeTransport(Protocol):
    async def invoke(self, operation: str, payload: dict[str, Any],
                     operation_id: str | None = None) -> dict[str, Any]: ...


_CAPABILITY_BY_OPERATION = {
    "fork": "can_fork",
    "navigate": "can_navigate",
    "inspect": "can_read_transcript",
    "archive": "can_archive",
    "rename": "can_rename",
}


def _binding_payload(binding: HostBinding) -> dict[str, Any]:
    return {
        "workflowId": binding.workflow_id,
        "instanceId": binding.instance_id,
        "threadId": binding.thread_id,
        "provider": binding.provider,
        "providerConversationId": binding.provider_conversation_id,
        "metadata": binding.metadata,
    }


class BridgeHostAdapter:
    def __init__(self, *, adapter_id: str, host_kind: str, display_name: str,
                 capabilities: HostCapabilities, transport: HostBridgeTransport,
                 limitations: tuple[str, ...] = (), connected: bool = True) -> None:
        if host_kind not in {"codex", "claude-code"}:
            raise ValueError("bridge host kind must be codex or claude-code")
        self.adapter_id = adapter_id
        self.host_kind = host_kind
        self.display_name = display_name
        self._capabilities = capabilities
        self.transport = transport
        self.limitations = limitations
        self.connected = connected

    def descriptor(self) -> HostDescriptor:
        return HostDescriptor(
            adapter_id=self.adapter_id,
            host_kind=self.host_kind,
            display_name=self.display_name,
            capabilities=self._capabilities,
            connected=self.connected,
            limitations=self.limitations,
        )

    def capabilities(self) -> HostCapabilities:
        return self._capabilities

    def _require(self, operation: str) -> None:
        capability = _CAPABILITY_BY_OPERATION[operation]
        if not bool(getattr(self._capabilities, capability)):
            raise HostCapabilityUnsupported(self.adapter_id, capability)

    def _binding(self, payload: Any, *, workflow_id: str | None = None) -> HostBinding:
        if not isinstance(payload, dict):
            raise HostAdapterError("hostInvalidResponse", "Host returned no binding object")
        required = ("workflowId", "instanceId", "threadId")
        if any(not isinstance(payload.get(key), str) or not payload[key].strip()
               for key in required):
            raise HostAdapterError(
                "hostInvalidResponse", "Host binding identity is incomplete"
            )
        if workflow_id is not None and payload["workflowId"] != workflow_id:
            raise HostAdapterError(
                "hostBindingMismatch", "Host returned a binding for another workflow"
            )
        provider = payload.get("provider")
        if provider is not None and provider not in {self.host_kind, self.adapter_id}:
            raise HostAdapterError(
                "hostBindingMismatch", "Host returned a binding for another provider"
            )
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        provider_conversation_id = payload.get("providerConversationId")
        return HostBinding(
            workflow_id=payload["workflowId"],
            instance_id=payload["instanceId"],
            thread_id=payload["threadId"],
            provider=self.host_kind,
            provider_conversation_id=(provider_conversation_id
                                      if isinstance(provider_conversation_id, str) else None),
            metadata=metadata,
        )

    @staticmethod
    def _page(payload: Any) -> Page:
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise HostAdapterError("hostInvalidResponse", "Host returned an invalid page")
        cursor = payload.get("nextCursor")
        if cursor is not None and not isinstance(cursor, str):
            raise HostAdapterError("hostInvalidResponse", "Host returned an invalid cursor")
        return Page(
            items=tuple(item for item in payload["items"] if isinstance(item, dict)),
            next_cursor=cursor,
        )

    async def resolve_current_context(self, request_context: dict[str, Any]) -> HostContext:
        payload = await self.transport.invoke("resolveCurrentContext", {
            "requestContext": request_context,
        })
        if not isinstance(payload, dict):
            raise HostAdapterError("hostInvalidResponse", "Host returned invalid context")
        workflow_id = payload.get("workflowId")
        instance_id = payload.get("instanceId")
        route = payload.get("memoryRoute")
        metadata = payload.get("metadata")
        if route is not None and not isinstance(route, list):
            raise HostAdapterError("hostInvalidResponse", "Host returned an invalid memory route")
        return HostContext(
            workflow_id=workflow_id if isinstance(workflow_id, str) else None,
            instance_id=instance_id if isinstance(instance_id, str) else None,
            memory_route=tuple(item for item in route or [] if isinstance(item, dict)),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    async def list_conversations(self, cursor: str | None = None) -> Page:
        return self._page(await self.transport.invoke(
            "listConversations", {"cursor": cursor}
        ))

    async def fork(self, source: HostBinding, checkpoint: dict[str, Any] | None,
                   prompt: str | None, options: dict[str, Any],
                   operation_id: str) -> HostBinding:
        self._require("fork")
        cursor_kind = checkpoint.get("kind") if checkpoint else None
        if (checkpoint and cursor_kind != "instanceHead"
                and not self._capabilities.can_fork_from_checkpoint):
            raise HostCapabilityUnsupported(self.adapter_id, "can_fork_from_checkpoint")
        if (cursor_kind is not None
                and cursor_kind not in self._capabilities.supported_checkpoint_cursor_kinds):
            raise HostCapabilityUnsupported(
                self.adapter_id, f"checkpoint_cursor:{cursor_kind}"
            )
        payload = await self.transport.invoke("fork", {
            "source": _binding_payload(source),
            "checkpoint": checkpoint,
            "prompt": prompt,
            "options": options,
        }, operation_id)
        return self._binding(payload.get("binding") if isinstance(payload, dict) else None,
                             workflow_id=source.workflow_id)

    async def navigate(self, binding: HostBinding, operation_id: str) -> HostResult:
        self._require("navigate")
        payload = await self.transport.invoke(
            "navigate", {"binding": _binding_payload(binding)}, operation_id
        )
        return self._result(payload, binding)

    async def inspect(self, binding: HostBinding, cursor: str | None = None,
                      limit: int = 50) -> Page:
        self._require("inspect")
        if limit < 1 or limit > 200:
            raise HostAdapterError("hostInvalidRequest", "inspect limit must be 1..200")
        payload = await self.transport.invoke("inspect", {
            "binding": _binding_payload(binding), "cursor": cursor, "limit": limit,
        })
        return self._page(payload)

    async def archive(self, binding: HostBinding, operation_id: str) -> HostResult:
        self._require("archive")
        payload = await self.transport.invoke(
            "archive", {"binding": _binding_payload(binding)}, operation_id
        )
        return self._result(payload, binding)

    async def rename(self, binding: HostBinding, title: str,
                     operation_id: str) -> HostResult:
        self._require("rename")
        if not title.strip():
            raise HostAdapterError("hostInvalidRequest", "title cannot be empty")
        payload = await self.transport.invoke("rename", {
            "binding": _binding_payload(binding), "title": title,
        }, operation_id)
        return self._result(payload, binding)

    def _result(self, payload: Any, fallback: HostBinding) -> HostResult:
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise HostAdapterError("hostInvalidResponse", "Host returned an invalid result")
        binding_payload = payload.get("binding")
        binding = (self._binding(binding_payload, workflow_id=fallback.workflow_id)
                   if binding_payload is not None else fallback)
        code = payload.get("code")
        message = payload.get("message")
        data = payload.get("data")
        return HostResult(
            ok=payload["ok"],
            code=code if isinstance(code, str) else None,
            message=message if isinstance(message, str) else None,
            binding=binding,
            data=data if isinstance(data, dict) else {},
        )


class CodexHostAdapter(BridgeHostAdapter):
    def __init__(self, transport: HostBridgeTransport, capabilities: HostCapabilities,
                 *, adapter_id: str = "codex-bridge", connected: bool = True,
                 limitations: tuple[str, ...] | None = None) -> None:
        super().__init__(
            adapter_id=adapter_id, host_kind="codex", display_name="Codex",
            capabilities=capabilities, transport=transport,
            connected=connected,
            limitations=limitations or ("Requires an installed trusted Codex companion bridge",),
        )


class ClaudeCodeHostAdapter(BridgeHostAdapter):
    def __init__(self, transport: HostBridgeTransport, capabilities: HostCapabilities,
                 *, adapter_id: str = "claude-code-bridge", connected: bool = True,
                 limitations: tuple[str, ...] | None = None) -> None:
        super().__init__(
            adapter_id=adapter_id, host_kind="claude-code", display_name="Claude Code",
            capabilities=capabilities, transport=transport,
            connected=connected,
            limitations=limitations or ("Capabilities depend on the connected Claude Code companion",),
        )


__all__ = [
    "BridgeHostAdapter", "ClaudeCodeHostAdapter", "CodexHostAdapter", "HostBridgeTransport",
]
