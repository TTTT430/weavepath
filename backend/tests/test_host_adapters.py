from __future__ import annotations

import asyncio

from graph_core import GraphStore
from host_adapters import HostCapabilities, HostBinding, MockHostAdapter, StandaloneHostAdapter


def test_mock_host_capability_contract_and_navigation():
    async def scenario():
        host = MockHostAdapter()
        assert host.capabilities().as_dict()["canNavigate"] is True
        source = HostBinding("wf", "root", "root")
        child = await host.fork(source, None, "branch", {}, "op-1")
        result = await host.navigate(child, "op-2")
        assert result.ok is True
        assert (await host.inspect(child)).items[0]["content"] == "branch"

    asyncio.run(scenario())


def test_standalone_host_resolves_graph_context():
    async def scenario():
        graph = GraphStore(":memory:")
        created = graph.create_workflow(name="W", root_title="A", root_instance_id="A")
        host = StandaloneHostAdapter(graph)
        context = await host.resolve_current_context({"workflowId": created["workflowId"], "instanceId": "A"})
        assert context.instance_id == "A"
        assert context.memory_route[0]["instanceId"] == "A"
        graph.close()

    asyncio.run(scenario())


def test_capability_defaults_are_explicit():
    caps = HostCapabilities()
    assert caps.as_dict()["canNavigate"] is False
    assert caps.as_dict()["supportedCheckpointCursorKinds"] == []
