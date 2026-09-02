from __future__ import annotations

from typing import Any

from host_adapters.ports import HostBinding, HostCapabilities, HostContext, HostResult, Page


class MockHostAdapter:
    """Deterministic host for adapter contract and saga tests."""

    def __init__(self, *, capabilities: HostCapabilities | None = None) -> None:
        self._capabilities = capabilities or HostCapabilities(
            can_fork=True, can_fork_from_checkpoint=True, can_navigate=True,
            can_read_transcript=True, can_read_local_turns=True, can_archive=True,
            can_rename=True, supported_checkpoint_cursor_kinds=("instanceHead", "localUserTurn"),
        )
        self.bindings: dict[str, HostBinding] = {}
        self.transcripts: dict[str, list[dict[str, Any]]] = {}
        self.operations: list[tuple[str, str, str]] = []

    def capabilities(self) -> HostCapabilities:
        return self._capabilities

    async def resolve_current_context(self, request_context: dict[str, Any]) -> HostContext:
        return HostContext(workflow_id=request_context.get("workflowId"),
                           instance_id=request_context.get("instanceId"), metadata={"mock": True})

    async def list_conversations(self, cursor: str | None = None) -> Page:
        del cursor
        return Page(items=tuple({"threadId": key, **binding.metadata}
                                for key, binding in self.bindings.items()))

    async def fork(self, source: HostBinding, checkpoint: dict[str, Any] | None,
                   prompt: str | None, options: dict[str, Any], operation_id: str) -> HostBinding:
        del checkpoint, options
        thread_id = f"mock-{len(self.bindings) + 1}"
        binding = HostBinding(source.workflow_id, thread_id, thread_id, provider="mock")
        self.bindings[thread_id] = binding
        self.transcripts[thread_id] = ([{"role": "user", "content": prompt}] if prompt else [])
        self.operations.append((operation_id, "fork", thread_id))
        return binding

    async def navigate(self, binding: HostBinding, operation_id: str) -> HostResult:
        self.operations.append((operation_id, "navigate", binding.thread_id))
        if binding.thread_id not in self.bindings:
            return HostResult(ok=False, code="notFound", message="mock thread not found")
        return HostResult(ok=True, binding=binding)

    async def inspect(self, binding: HostBinding, cursor: str | None = None,
                      limit: int = 50) -> Page:
        del cursor
        return Page(items=tuple(self.transcripts.get(binding.thread_id, [])[:limit]))

    async def archive(self, binding: HostBinding, operation_id: str) -> HostResult:
        self.operations.append((operation_id, "archive", binding.thread_id))
        self.bindings.pop(binding.thread_id, None)
        self.transcripts.pop(binding.thread_id, None)
        return HostResult(ok=True, binding=binding)

    async def rename(self, binding: HostBinding, title: str, operation_id: str) -> HostResult:
        self.operations.append((operation_id, "rename", binding.thread_id))
        self.bindings[binding.thread_id] = HostBinding(
            binding.workflow_id, binding.instance_id, binding.thread_id,
            binding.provider, binding.provider_conversation_id, {**binding.metadata, "title": title},
        )
        return HostResult(ok=True, binding=self.bindings[binding.thread_id])
