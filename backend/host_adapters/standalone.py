from __future__ import annotations

from typing import Any

from graph_core import GraphStore, NotFound, Validation
from host_adapters.ports import HostBinding, HostCapabilities, HostContext, HostResult, Page


class StandaloneHostAdapter:
    """Adapter for the local GraphStore-backed workspace.

    It deliberately delegates graph semantics to GraphStore. The adapter only
    translates the host-facing contract and never invents parent/topic/prune
    relationships.
    """

    def __init__(self, graph: GraphStore) -> None:
        self.graph = graph

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(
            can_fork=True, can_fork_from_checkpoint=True, can_navigate=True,
            can_read_transcript=True, can_read_local_turns=True, can_archive=True,
            can_rename=True, can_open_external_window=False,
            supported_checkpoint_cursor_kinds=("instanceHead", "localUserTurn"),
        )

    async def resolve_current_context(self, request_context: dict[str, Any]) -> HostContext:
        workflow_id = request_context.get("workflowId")
        instance_id = request_context.get("instanceId")
        if not workflow_id or not instance_id:
            return HostContext(metadata={"resolved": False})
        graph = self.graph.get_graph(str(workflow_id))
        node = next((item for item in graph["nodes"] if item["id"] == instance_id), None)
        if node is None:
            raise NotFound("conversation instance not found")
        by_id = {item["id"]: item for item in graph["nodes"]}
        memory_route = tuple({"instanceId": route_id,
                              "title": by_id.get(route_id, {}).get("title")}
                             for route_id in node.get("memoryRoute", []))
        return HostContext(workflow_id=str(workflow_id), instance_id=str(instance_id),
                           memory_route=memory_route,
                           metadata={"provider": node.get("provider", "standalone")})

    async def list_conversations(self, cursor: str | None = None) -> Page:
        del cursor
        workflows = self.graph.list_workflows()["workflows"]
        items = tuple(node for workflow in workflows for node in workflow.get("nodes", []))
        return Page(items=items)

    async def fork(self, source: HostBinding, checkpoint: dict[str, Any] | None,
                   prompt: str | None, options: dict[str, Any], operation_id: str) -> HostBinding:
        del operation_id
        kwargs = dict(options)
        if checkpoint:
            kwargs.setdefault("anchor_message_id", checkpoint.get("anchorMessageId"))
        if prompt:
            kwargs.setdefault("initial_message", prompt)
        result = self.graph.fork(source.workflow_id, source.instance_id, **kwargs)
        node = result["node"]
        return HostBinding(source.workflow_id, node["id"], node["id"],
                           provider=node.get("provider", "standalone"),
                           provider_conversation_id=node.get("providerConversationId"))

    async def navigate(self, binding: HostBinding, operation_id: str) -> HostResult:
        del operation_id
        self.graph.activate(binding.workflow_id, binding.instance_id)
        return HostResult(ok=True, binding=binding,
                          data={"workflowId": binding.workflow_id, "instanceId": binding.instance_id})

    async def inspect(self, binding: HostBinding, cursor: str | None = None,
                      limit: int = 50) -> Page:
        del cursor
        if limit < 1:
            raise Validation("limit must be positive")
        messages = self.graph.list_messages(binding.workflow_id, binding.instance_id, scope="local")["messages"]
        return Page(items=tuple(messages[:limit]), next_cursor=None)

    async def archive(self, binding: HostBinding, operation_id: str) -> HostResult:
        del operation_id
        plan = self.graph.prune_plan(binding.workflow_id, binding.instance_id)
        self.graph.prune_commit(binding.workflow_id, binding.instance_id,
                                 expected_revision=plan["graphRevision"],
                                 idempotency_key=f"host-archive-{binding.instance_id}")
        return HostResult(ok=True, binding=binding)

    async def rename(self, binding: HostBinding, title: str, operation_id: str) -> HostResult:
        del operation_id
        graph = self.graph.get_graph(binding.workflow_id)
        node = next(item for item in graph["nodes"] if item["id"] == binding.instance_id)
        self.graph.rename_instance(binding.workflow_id, binding.instance_id, title=title,
                                    expected_revision=graph["graphRevision"])
        return HostResult(ok=True, binding=binding)
