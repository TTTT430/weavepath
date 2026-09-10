from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from graph_core import GraphStore
from host_adapters import (
    HOST_ADAPTER_CONTRACT_VERSION,
    CodexHostAdapter,
    HostAdapterError,
    HostCapabilities,
    HostBinding,
    HostCapabilityUnsupported,
    MockHostAdapter,
    StandaloneHostAdapter,
)


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.responses = {}

    async def invoke(self, operation, payload, operation_id=None):
        self.calls.append((operation, payload, operation_id))
        return self.responses.get(operation, {})


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


def test_standalone_descriptor_versions_the_host_contract():
    graph = GraphStore(":memory:")
    descriptor = StandaloneHostAdapter(graph).descriptor().as_dict()
    assert descriptor["adapterId"] == "standalone"
    assert descriptor["contractVersion"] == HOST_ADAPTER_CONTRACT_VERSION
    assert descriptor["hostKind"] == "standalone"
    assert descriptor["capabilities"]["canReadLocalTurns"] is True
    with TestClient(create_app(graph)) as client:
        payload = client.get("/api/v1/host/capabilities").json()
        assert payload["adapter"] == "standalone"
        assert payload["capabilities"]["canNavigate"] is True
        assert payload["descriptor"]["contractVersion"] == 1
        assert payload["descriptor"]["hostKind"] == "standalone"
    graph.close()


def test_codex_bridge_validates_bindings_and_forwards_operation_identity():
    async def scenario():
        transport = FakeBridge()
        transport.responses["fork"] = {"binding": {
            "workflowId": "wf", "instanceId": "child", "threadId": "thread-2",
            "provider": "codex", "providerConversationId": "thread-2",
        }}
        transport.responses["navigate"] = {"ok": True, "data": {"activated": True}}
        host = CodexHostAdapter(transport, HostCapabilities(
            can_fork=True, can_fork_from_checkpoint=True, can_navigate=True,
            supported_checkpoint_cursor_kinds=("localUserTurn",),
        ))
        source = HostBinding("wf", "root", "thread-1", provider="codex")
        child = await host.fork(
            source, {"kind": "localUserTurn", "anchorMessageId": 7},
            "continue", {}, "operation-fork-1",
        )
        result = await host.navigate(child, "operation-nav-1")
        assert child.instance_id == "child"
        assert child.provider == "codex"
        assert result.ok is True
        assert transport.calls[0][2] == "operation-fork-1"
        assert transport.calls[1][2] == "operation-nav-1"

    asyncio.run(scenario())


def test_bridge_rejects_unsupported_capabilities_before_transport_call():
    async def scenario():
        transport = FakeBridge()
        host = CodexHostAdapter(transport, HostCapabilities())
        binding = HostBinding("wf", "root", "thread-1", provider="codex")
        with pytest.raises(HostCapabilityUnsupported) as raised:
            await host.navigate(binding, "op")
        assert raised.value.code == "hostCapabilityUnsupported"
        assert transport.calls == []

        cursor_host = CodexHostAdapter(transport, HostCapabilities(
            can_fork=True, can_fork_from_checkpoint=True,
        ))
        with pytest.raises(HostCapabilityUnsupported) as cursor_error:
            await cursor_host.fork(
                binding, {"kind": "localUserTurn"}, None, {}, "fork-op"
            )
        assert cursor_error.value.capability == "checkpoint_cursor:localUserTurn"
        assert transport.calls == []

    asyncio.run(scenario())


def test_bridge_rejects_cross_workflow_binding_from_companion():
    async def scenario():
        transport = FakeBridge()
        transport.responses["fork"] = {"binding": {
            "workflowId": "other", "instanceId": "child", "threadId": "thread-2",
            "provider": "codex",
        }}
        host = CodexHostAdapter(transport, HostCapabilities(can_fork=True))
        with pytest.raises(HostAdapterError) as raised:
            await host.fork(
                HostBinding("wf", "root", "thread-1", provider="codex"),
                None, None, {}, "op",
            )
        assert raised.value.code == "hostBindingMismatch"

    asyncio.run(scenario())
