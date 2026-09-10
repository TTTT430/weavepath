from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from graph_core import Conflict, GraphStore
from host_adapters import (
    HOST_ADAPTER_CONTRACT_VERSION,
    CodexHostAdapter,
    HostAdapterError,
    HostCapabilities,
    HostBinding,
    HostCapabilityUnsupported,
    HostOperationJournal,
    HttpHostBridgeTransport,
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


def test_host_conversations_can_be_listed_and_imported_once_without_copying_transcript():
    graph = GraphStore(":memory:")
    bridge = FakeBridge()
    bridge.responses["listConversations"] = {
        "items": [{
            "threadId": "thread-existing",
            "providerConversationId": "thread-existing",
            "title": "Existing Codex task",
        }],
        "nextCursor": None,
    }
    bridge.responses["inspect"] = {
        "items": [{"role": "user", "content": "host-owned"}],
        "nextCursor": None,
    }
    host = CodexHostAdapter(
        bridge, HostCapabilities(can_read_transcript=True), connected=True
    )
    with TestClient(create_app(graph, host_adapter=host)) as client:
        listed = client.get("/api/v1/host/conversations")
        assert listed.status_code == 200
        assert listed.json()["host"]["hostKind"] == "codex"
        assert listed.json()["items"][0]["threadId"] == "thread-existing"

        first = client.post("/api/v1/host/conversations/import", json={
            "threadId": "thread-existing", "title": "Imported task",
        })
        assert first.status_code == 201
        assert first.json()["imported"] is True
        node = first.json()["node"]
        assert node["provider"] == "codex"
        assert node["providerConversationId"] == "thread-existing"
        assert graph.list_messages(
            first.json()["graph"]["workflowId"], node["id"], scope="local"
        )["messages"] == []

        replay = client.post("/api/v1/host/conversations/import", json={
            "threadId": "thread-existing", "title": "Ignored duplicate",
        })
        assert replay.status_code == 201
        assert replay.json()["imported"] is False
    assert [call[0] for call in bridge.calls] == ["listConversations", "inspect"]
    graph.close()


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


def test_http_transport_authenticates_and_validates_the_companion_envelope():
    async def scenario():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer secret-token"
            assert request.url.host == "127.0.0.1"
            if request.url.path == "/v1/handshake":
                return httpx.Response(200, json={
                    "contractVersion": 1, "hostKind": "codex",
                    "capabilities": {"canNavigate": True},
                })
            body = __import__("json").loads(request.content)
            assert body["contractVersion"] == 1
            assert body["operationId"] == "op-1"
            return httpx.Response(200, json={
                "contractVersion": 1, "hostKind": "codex",
                "result": {"ok": True, "data": {"activated": True}},
            })

        transport = HttpHostBridgeTransport(
            "http://127.0.0.1:43123", "secret-token", "codex",
            transport=httpx.MockTransport(handler),
        )
        handshake = await transport.handshake()
        assert handshake["capabilities"]["canNavigate"] is True
        result = await transport.invoke("navigate", {"binding": {}}, "op-1")
        assert result["data"]["activated"] is True

    asyncio.run(scenario())


def test_http_transport_rejects_unencrypted_non_loopback_bridge():
    with pytest.raises(HostAdapterError) as raised:
        HttpHostBridgeTransport("http://example.com", "token", "codex")
    assert raised.value.code == "hostBridgeConfigurationInvalid"


def test_http_transport_preserves_a_structured_companion_error():
    async def scenario():
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={
                "error": {"code": "hostThreadBusy", "message": "Thread is still running"}
            })

        transport = HttpHostBridgeTransport(
            "http://127.0.0.1:43123", "secret-token", "codex",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(HostAdapterError) as raised:
            await transport.invoke("navigate", {}, "op-busy")
        assert raised.value.code == "hostThreadBusy"
        assert str(raised.value) == "Thread is still running"

    asyncio.run(scenario())


def test_host_operation_journal_is_durable_idempotent_and_quarantines_unknown_outcomes():
    graph = GraphStore(":memory:")
    created = graph.create_workflow(name="W", root_title="A", root_instance_id="root")
    journal = HostOperationJournal(graph._conn, graph._lock)
    first, was_created = journal.begin(
        workflow_id=created["workflowId"], source_instance_id="root",
        operation_type="fork", host_kind="codex", idempotency_key="stable-key",
        request={"title": "B"}, target_instance_id="child",
    )
    replay, replay_created = journal.begin(
        workflow_id=created["workflowId"], source_instance_id="root",
        operation_type="fork", host_kind="codex", idempotency_key="stable-key",
        request={"title": "B"}, target_instance_id="child",
    )
    assert was_created is True and replay_created is False
    assert replay["operationId"] == first["operationId"]
    with pytest.raises(Exception):
        journal.begin(
            workflow_id=created["workflowId"], source_instance_id="root",
            operation_type="fork", host_kind="codex", idempotency_key="stable-key",
            request={"title": "different"}, target_instance_id="child",
        )
    assert journal.recover_started() == 1
    assert journal.get(first["operationId"])["status"] == "orphaned"
    graph.close()


def test_host_bound_fork_registers_the_remote_child_and_replays_the_saga():
    graph = GraphStore(":memory:")
    created = graph.create_workflow(
        name="W", root_title="A", root_instance_id="root",
        provider="codex", provider_conversation_id="thread-root",
    )
    bridge = FakeBridge()
    bridge.responses["fork"] = {"binding": {
        "workflowId": created["workflowId"], "instanceId": "child",
        "threadId": "thread-child", "provider": "codex",
        "providerConversationId": "thread-child",
    }}
    bridge.responses["navigate"] = {"ok": True, "data": {"activated": True}}
    host = CodexHostAdapter(bridge, HostCapabilities(
        can_fork=True, can_navigate=True,
        supported_checkpoint_cursor_kinds=("instanceHead",),
    ))
    with TestClient(create_app(graph, host_adapter=host)) as client:
        body = {"instanceId": "child", "title": "B", "idempotencyKey": "fork-1"}
        first = client.post(
            f"/api/v1/workflows/{created['workflowId']}/instances/root/fork", json=body
        )
        assert first.status_code == 201
        assert first.json()["node"]["id"] == "child"
        replay = client.post(
            f"/api/v1/workflows/{created['workflowId']}/instances/root/fork", json=body
        )
        assert replay.status_code == 201
        assert replay.json()["node"]["providerConversationId"] == "thread-child"
    assert [call[0] for call in bridge.calls] == ["fork", "navigate"]
    graph.close()


def test_host_bound_fork_archives_the_remote_child_when_local_registration_conflicts():
    graph = GraphStore(":memory:")
    created = graph.create_workflow(
        name="W", root_title="A", root_instance_id="root",
        provider="codex", provider_conversation_id="thread-root",
    )
    bridge = FakeBridge()
    bridge.responses["fork"] = {"binding": {
        "workflowId": created["workflowId"], "instanceId": "child",
        "threadId": "thread-child", "provider": "codex",
    }}
    bridge.responses["archive"] = {"ok": True, "data": {"archived": True}}
    host = CodexHostAdapter(bridge, HostCapabilities(
        can_fork=True, can_archive=True,
        supported_checkpoint_cursor_kinds=("instanceHead",),
    ))
    original_fork = graph.fork

    def conflicting_registration(*args, **kwargs):
        if kwargs.get("provider") == "codex":
            raise Conflict("concurrent local registration")
        return original_fork(*args, **kwargs)

    graph.fork = conflicting_registration
    with TestClient(create_app(graph, host_adapter=host)) as client:
        response = client.post(
            f"/api/v1/workflows/{created['workflowId']}/instances/root/fork",
            json={"instanceId": "child", "title": "B", "expectedContentRevision": 0,
                  "idempotencyKey": "fork-conflict"},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "hostForkRegistrationFailed"
    assert [call[0] for call in bridge.calls] == ["fork", "archive"]
    graph.close()


def test_host_bound_fork_rejects_a_stale_revision_before_calling_the_host():
    graph = GraphStore(":memory:")
    created = graph.create_workflow(
        name="W", root_title="A", root_instance_id="root",
        provider="codex", provider_conversation_id="thread-root",
    )
    bridge = FakeBridge()
    host = CodexHostAdapter(bridge, HostCapabilities(
        can_fork=True, supported_checkpoint_cursor_kinds=("instanceHead",),
    ))
    with TestClient(create_app(graph, host_adapter=host)) as client:
        response = client.post(
            f"/api/v1/workflows/{created['workflowId']}/instances/root/fork",
            json={"instanceId": "child", "title": "B", "expectedContentRevision": 99,
                  "idempotencyKey": "stale-fork"},
        )
        assert response.status_code == 409
    assert bridge.calls == []
    graph.close()


def test_host_succeeded_rename_saga_finishes_locally_without_repeating_the_host_call():
    graph = GraphStore(":memory:")
    created = graph.create_workflow(
        name="W", root_title="A", root_instance_id="root",
        provider="codex", provider_conversation_id="thread-root",
    )
    bridge = FakeBridge()
    host = CodexHostAdapter(bridge, HostCapabilities(can_rename=True))
    journal = HostOperationJournal(graph._conn, graph._lock)
    body = {"title": "B", "expectedRevision": 1}
    key = hashlib.sha256(b"root:1:B").hexdigest()
    saga, _ = journal.begin(
        workflow_id=created["workflowId"], source_instance_id="root",
        operation_type="rename", host_kind="codex", idempotency_key=key,
        request=body, target_instance_id="root",
    )
    journal.transition(saga["operationId"], "host_succeeded", host_result={"result": {}})
    with TestClient(create_app(graph, host_adapter=host)) as client:
        response = client.patch(
            f"/api/v1/workflows/{created['workflowId']}/instances/root", json=body
        )
        assert response.status_code == 200
        assert response.json()["node"]["title"] == "B"
    assert bridge.calls == []
    assert journal.get(saga["operationId"])["status"] == "completed"
    graph.close()


def test_host_succeeded_navigation_saga_finishes_local_activation_without_repeating_host():
    graph = GraphStore(":memory:")
    created = graph.create_workflow(
        name="W", root_title="A", root_instance_id="root",
        provider="codex", provider_conversation_id="thread-root",
    )
    child = graph.fork(
        created["workflowId"], "root", instance_id="child", title="B",
        provider="codex", provider_conversation_id="thread-child",
    )
    graph.activate(created["workflowId"], "root")
    bridge = FakeBridge()
    host = CodexHostAdapter(bridge, HostCapabilities(can_navigate=True))
    journal = HostOperationJournal(graph._conn, graph._lock)
    request = {"instanceId": child["node"]["id"]}
    saga, _ = journal.begin(
        workflow_id=created["workflowId"], source_instance_id="child",
        operation_type="navigate", host_kind="codex", idempotency_key="nav-recovery",
        request=request, target_instance_id="child",
    )
    journal.transition(saga["operationId"], "host_succeeded", host_result={"result": {}})
    with TestClient(create_app(graph, host_adapter=host)) as client:
        response = client.post(
            f"/api/v1/workflows/{created['workflowId']}/instances/child/activate",
            json={"preferenceKey": "nav-recovery"},
        )
        assert response.status_code == 200
        assert response.json()["activeInstanceId"] == "child"
    assert bridge.calls == []
    assert journal.get(saga["operationId"])["status"] == "completed"
    graph.close()
