from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from graph_core import GraphStore
from host_adapters.bridge import ClaudeCodeHostAdapter, CodexHostAdapter
from host_adapters.ports import (
    HOST_ADAPTER_CONTRACT_VERSION,
    HostAdapter,
    HostAdapterError,
    HostCapabilities,
)
from host_adapters.standalone import StandaloneHostAdapter


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_bridge_url(value: str) -> str:
    parsed = urlparse(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HostAdapterError("hostBridgeConfigurationInvalid", "Host bridge URL is invalid")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise HostAdapterError(
            "hostBridgeConfigurationInvalid",
            "Plain HTTP host bridges are allowed only on a loopback address",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HostAdapterError("hostBridgeConfigurationInvalid", "Host bridge URL must not contain credentials, query, or fragment")
    return value.rstrip("/")


def capabilities_from_dict(value: Any) -> HostCapabilities:
    data = value if isinstance(value, dict) else {}
    cursors = data.get("supportedCheckpointCursorKinds")
    return HostCapabilities(
        can_fork=data.get("canFork") is True,
        can_fork_from_checkpoint=data.get("canForkFromCheckpoint") is True,
        can_navigate=data.get("canNavigate") is True,
        can_read_transcript=data.get("canReadTranscript") is True,
        can_read_local_turns=data.get("canReadLocalTurns") is True,
        can_archive=data.get("canArchive") is True,
        can_rename=data.get("canRename") is True,
        can_open_external_window=data.get("canOpenExternalWindow") is True,
        supported_checkpoint_cursor_kinds=tuple(
            item for item in cursors or [] if isinstance(item, str) and item
        ),
    )


class HttpHostBridgeTransport:
    """Authenticated contract-v1 transport to a local host companion."""

    def __init__(self, base_url: str, token: str, host_kind: str,
                 *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout_seconds: float | None = None) -> None:
        if host_kind not in {"codex", "claude-code"}:
            raise HostAdapterError("hostBridgeConfigurationInvalid", "Unknown host bridge kind")
        if not token.strip():
            raise HostAdapterError("hostBridgeConfigurationInvalid", "Host bridge token is missing")
        self.base_url = _validate_bridge_url(base_url)
        self.token = token
        self.host_kind = host_kind
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    @staticmethod
    def _raise_bridge_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "hostOperationFailed")
                message = str(error.get("message") or "Host companion operation failed")
                raise HostAdapterError(code, message)
            if isinstance(payload.get("code"), str):
                raise HostAdapterError(
                    payload["code"],
                    str(payload.get("message") or "Host companion operation failed"),
                )
        raise HostAdapterError(
            "hostUnauthorized" if response.status_code in {401, 403} else "hostOperationFailed",
            "Host companion rejected the request",
        )

    @staticmethod
    def _validate_envelope(data: Any, expected_host_kind: str) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise HostAdapterError("hostInvalidResponse", "Host bridge returned non-object JSON")
        if data.get("contractVersion") != HOST_ADAPTER_CONTRACT_VERSION:
            raise HostAdapterError("hostContractMismatch", "Host bridge contract version does not match")
        if data.get("hostKind") != expected_host_kind:
            raise HostAdapterError("hostBindingMismatch", "Host bridge identity does not match")
        return data

    async def handshake(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport, trust_env=False
        ) as client:
            try:
                response = await client.get(f"{self.base_url}/v1/handshake", headers=self._headers())
                self._raise_bridge_error(response)
                return self._validate_envelope(response.json(), self.host_kind)
            except HostAdapterError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise HostAdapterError("hostDisconnected", "Unable to connect to the host companion") from exc

    async def invoke(self, operation: str, payload: dict[str, Any],
                     operation_id: str | None = None) -> dict[str, Any]:
        if operation not in {"resolveCurrentContext", "listConversations", "fork", "navigate", "inspect", "archive", "rename"}:
            raise HostAdapterError("hostInvalidRequest", "Unknown host operation")
        body = {
            "contractVersion": HOST_ADAPTER_CONTRACT_VERSION,
            "operationId": operation_id,
            "payload": payload,
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport, trust_env=False
        ) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v1/operations/{operation}", headers=self._headers(), json=body
                )
                self._raise_bridge_error(response)
                envelope = self._validate_envelope(response.json(), self.host_kind)
            except HostAdapterError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise HostAdapterError("hostDisconnected", "Host companion operation failed") from exc
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise HostAdapterError("hostInvalidResponse", "Host bridge result is invalid")
        return result


def _default_discovery_path() -> Path:
    configured = os.getenv("WEAVEPATH_HOST_BRIDGE_DISCOVERY")
    if configured:
        return Path(configured)
    local = os.getenv("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".local" / "share"
    return base / "WeavePath" / "host-bridge.json"


def read_bridge_configuration() -> dict[str, str] | None:
    host_kind = os.getenv("WEAVEPATH_HOST_KIND", "").strip()
    base_url = os.getenv("WEAVEPATH_HOST_BRIDGE_URL", "").strip()
    token = os.getenv("WEAVEPATH_HOST_BRIDGE_TOKEN", "").strip()
    if not (host_kind or base_url or token):
        path = _default_discovery_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        host_kind = str(data.get("hostKind") or "").strip()
        base_url = str(data.get("baseUrl") or "").strip()
        token = str(data.get("token") or "").strip()
    if not (host_kind and base_url and token):
        return None
    return {"hostKind": host_kind, "baseUrl": _validate_bridge_url(base_url), "token": token}


def configured_host_adapter(graph: GraphStore) -> HostAdapter:
    """Select a discovered companion without making app startup depend on it.

    Capabilities are read from a short authenticated handshake. If it is not
    reachable, the descriptor remains visibly disconnected with all mutation
    capabilities disabled instead of silently falling back to local behavior.
    """
    config = read_bridge_configuration()
    if config is None:
        return StandaloneHostAdapter(graph)
    kind = config["hostKind"]
    transport = HttpHostBridgeTransport(config["baseUrl"], config["token"], kind)
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            response = client.get(
                f"{config['baseUrl']}/v1/handshake",
                headers={"Authorization": f"Bearer {config['token']}", "Accept": "application/json"},
            )
            response.raise_for_status()
            envelope = HttpHostBridgeTransport._validate_envelope(response.json(), kind)
        caps = capabilities_from_dict(envelope.get("capabilities"))
        connected = envelope.get("connected") is not False
        limitations = tuple(item for item in envelope.get("limitations", []) if isinstance(item, str))
        if kind == "codex":
            return CodexHostAdapter(transport, caps, connected=connected, limitations=limitations)
        if kind == "claude-code":
            return ClaudeCodeHostAdapter(transport, caps, connected=connected, limitations=limitations)
    except (httpx.HTTPError, ValueError, HostAdapterError):
        caps = HostCapabilities()
        limitation = ("Configured host companion is currently unreachable",)
        if kind == "codex":
            return CodexHostAdapter(transport, caps, connected=False, limitations=limitation)
        if kind == "claude-code":
            return ClaudeCodeHostAdapter(transport, caps, connected=False, limitations=limitation)
    raise HostAdapterError("hostBridgeConfigurationInvalid", "Unknown host bridge kind")


__all__ = [
    "HttpHostBridgeTransport", "capabilities_from_dict", "configured_host_adapter",
    "read_bridge_configuration",
]
