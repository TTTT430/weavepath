from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


HOST_ADAPTER_CONTRACT_VERSION = 1


class HostAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HostCapabilityUnsupported(HostAdapterError):
    def __init__(self, adapter_id: str, capability: str) -> None:
        self.adapter_id = adapter_id
        self.capability = capability
        super().__init__(
            "hostCapabilityUnsupported",
            f"Host adapter '{adapter_id}' does not provide capability '{capability}'",
        )


@dataclass(frozen=True)
class HostCapabilities:
    can_fork: bool = False
    can_fork_from_checkpoint: bool = False
    can_navigate: bool = False
    can_read_transcript: bool = False
    can_read_local_turns: bool = False
    can_archive: bool = False
    can_rename: bool = False
    can_open_external_window: bool = False
    supported_checkpoint_cursor_kinds: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "canFork": self.can_fork,
            "canForkFromCheckpoint": self.can_fork_from_checkpoint,
            "canNavigate": self.can_navigate,
            "canReadTranscript": self.can_read_transcript,
            "canReadLocalTurns": self.can_read_local_turns,
            "canArchive": self.can_archive,
            "canRename": self.can_rename,
            "canOpenExternalWindow": self.can_open_external_window,
            "supportedCheckpointCursorKinds": list(self.supported_checkpoint_cursor_kinds),
        }


@dataclass(frozen=True)
class HostDescriptor:
    adapter_id: str
    host_kind: str
    display_name: str
    capabilities: HostCapabilities
    contract_version: int = HOST_ADAPTER_CONTRACT_VERSION
    connected: bool = True
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapterId": self.adapter_id,
            "hostKind": self.host_kind,
            "displayName": self.display_name,
            "contractVersion": self.contract_version,
            "connected": self.connected,
            "capabilities": self.capabilities.as_dict(),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class HostContext:
    workflow_id: str | None = None
    instance_id: str | None = None
    memory_route: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HostBinding:
    workflow_id: str
    instance_id: str
    thread_id: str
    provider: str = "standalone"
    provider_conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HostResult:
    ok: bool
    code: str | None = None
    message: str | None = None
    binding: HostBinding | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Page:
    items: tuple[dict[str, Any], ...]
    next_cursor: str | None = None


@runtime_checkable
class HostAdapter(Protocol):
    def descriptor(self) -> HostDescriptor: ...
    def capabilities(self) -> HostCapabilities: ...
    async def resolve_current_context(self, request_context: dict[str, Any]) -> HostContext: ...
    async def list_conversations(self, cursor: str | None = None) -> Page: ...
    async def fork(self, source: HostBinding, checkpoint: dict[str, Any] | None,
                   prompt: str | None, options: dict[str, Any], operation_id: str) -> HostBinding: ...
    async def navigate(self, binding: HostBinding, operation_id: str) -> HostResult: ...
    async def inspect(self, binding: HostBinding, cursor: str | None = None,
                      limit: int = 50) -> Page: ...
    async def archive(self, binding: HostBinding, operation_id: str) -> HostResult: ...
    async def rename(self, binding: HostBinding, title: str, operation_id: str) -> HostResult: ...
