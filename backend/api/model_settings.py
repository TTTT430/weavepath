from __future__ import annotations

import ipaddress
import json
import os
import socket
import ssl
from collections.abc import Iterator
from threading import Event
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from api.credential_store import CredentialStore, default_credential_store
from api.llm import LLMUnavailable, NetworkMode, OpenAICompatibleLLM


Persistence = Literal["memory", "local"]


class ModelSettingsError(LLMUnavailable):
    def __init__(self, code: str, message: str, status_code: int = 503,
                 diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=code, status_code=status_code)
        self.diagnostics = diagnostics


def validate_base_url(value: str) -> str:
    if len(value) > 2048:
        raise ValueError("baseUrl is too long")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("baseUrl must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("baseUrl cannot contain credentials, query, or fragment")
    if parsed.scheme == "http":
        host = parsed.hostname.lower()
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not loopback:
            raise ValueError("non-HTTPS baseUrl is allowed only for loopback hosts")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    # Connection/write timeout only. Model response reads have no deadline.
    timeout_seconds: float = 15.0
    system_prompt: str = ""
    network_mode: NetworkMode = "auto"


class RuntimeModelSettings:
    """Mutable runtime settings with opt-in OS-protected secret persistence."""

    def __init__(self, local_path: str | Path, env: dict[str, str] | None = None,
                 credential_store: CredentialStore | None = None) -> None:
        self.local_path = Path(local_path)
        self._env = dict(os.environ if env is None else env)
        self._credential_store = credential_store or default_credential_store(self.local_path)
        self._lock = threading.RLock()
        self._config: ModelConfig | None = None
        self._api_key = ""
        self._api_key_persisted = False
        self._credential_error = False
        self._source = "none"
        self._persistence: Persistence = "memory"
        self._load()

    def _load(self) -> None:
        base = (self._env.get("WEAVEPATH_LLM_BASE_URL")
                or self._env.get("COTHINKER_LLM_BASE_URL") or "").strip()
        model = (self._env.get("WEAVEPATH_LLM_MODEL")
                 or self._env.get("COTHINKER_LLM_MODEL") or "").strip()
        api_key = (self._env.get("WEAVEPATH_LLM_API_KEY")
                   or self._env.get("COTHINKER_LLM_API_KEY")
                   or self._env.get("OPENAI_API_KEY") or "").strip()
        if not base and api_key:
            base = "https://api.openai.com/v1"
        if base and model:
            try:
                self._config = ModelConfig(
                    validate_base_url(base), _model(model),
                    _timeout(self._env.get("WEAVEPATH_LLM_CONNECT_TIMEOUT")
                             or self._env.get("WEAVEPATH_LLM_TIMEOUT")
                             or self._env.get("COTHINKER_LLM_TIMEOUT") or "15"),
                    _prompt(self._env.get("WEAVEPATH_LLM_SYSTEM_PROMPT")
                            or self._env.get("COTHINKER_LLM_SYSTEM_PROMPT") or ""),
                    _network_mode(self._env.get("WEAVEPATH_LLM_NETWORK_MODE") or "auto"),
                )
            except (TypeError, ValueError):
                self._config = None
            else:
                self._api_key, self._source = api_key, "environment"
                return
        try:
            raw = json.loads(self.local_path.read_text(encoding="utf-8"))
            self._config = ModelConfig(
                validate_base_url(raw["baseUrl"]), str(raw["model"]),
                _timeout(raw.get("connectTimeoutSeconds", raw.get("timeoutSeconds", 15))),
                str(raw.get("systemPrompt", "")),
                _network_mode(raw.get("networkMode", "auto")),
            )
            self._source, self._persistence = "local", "local"
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
        self._api_key = api_key
        if not self._api_key and self._config is not None:
            try:
                self._api_key = self._credential_store.load().strip()
                self._api_key_persisted = bool(self._api_key)
            except (OSError, RuntimeError, ValueError, UnicodeError):
                # Corrupt/unavailable encrypted storage must not prevent the
                # workspace from starting or expose native error details.
                self._credential_error = True

    def status(self) -> dict[str, Any]:
        with self._lock:
            config = self._config
            configured = bool(config and config.base_url and config.model)
            return {
                "configured": configured,
                "provider": "openai-compatible",
                "baseUrl": config.base_url if config else None,
                "model": config.model if config else None,
                "networkMode": config.network_mode if config else "auto",
                "systemPrompt": config.system_prompt if config else "",
                "hasApiKey": bool(self._api_key),
                "apiKeyPersisted": self._api_key_persisted,
                "secureKeyStorageAvailable": bool(self._credential_store.available),
                "credentialError": self._credential_error,
                "secretPersistence": ("environment" if self._source == "environment" and self._api_key
                                      else "secure-local" if self._api_key_persisted
                                      else "memory" if self._api_key else "none"),
                "source": self._source,
                "persistence": self._persistence,
                "reason": None if configured else "AI provider is not configured",
            }

    def configure(self, *, base_url: str, model: str, api_key: str | None = None,
                  connect_timeout_seconds: float = 15.0, system_prompt: str = "",
                  persistence: Persistence = "memory", clear_api_key: bool = False,
                  persist_api_key: bool = False,
                  network_mode: NetworkMode = "auto") -> dict[str, Any]:
        config = ModelConfig(validate_base_url(base_url), _model(model),
                             _timeout(connect_timeout_seconds), _prompt(system_prompt),
                             _network_mode(network_mode))
        with self._lock:
            next_api_key = self._api_key
            if clear_api_key:
                next_api_key = ""
            elif api_key is not None and api_key.strip():
                next_api_key = api_key.strip()
            if persist_api_key:
                if persistence != "local":
                    raise ValueError("Secure API key persistence requires local settings persistence")
                if not self._credential_store.available:
                    raise ValueError("Secure API key storage is unavailable on this platform")
                if not next_api_key:
                    raise ValueError("An API key is required for secure persistence")
                self._credential_store.save(next_api_key)
                self._api_key_persisted = True
            else:
                self._credential_store.clear()
                self._api_key_persisted = False
            if persistence == "local":
                self._write_non_secret(config)
            self._config = config
            self._api_key = next_api_key
            self._source = "local" if persistence == "local" else "runtime"
            self._persistence = persistence
            self._credential_error = False
            return self.status()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._config, self._api_key, self._source, self._persistence = None, "", "none", "memory"
            self.local_path.unlink(missing_ok=True)
            self._credential_store.clear()
            self._api_key_persisted = False
            self._credential_error = False
            self._load()
            return self.status()

    def _write_non_secret(self, config: ModelConfig) -> None:
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.local_path.with_suffix(self.local_path.suffix + ".tmp")
        value = {
            "version": 2, "provider": "openai-compatible", "baseUrl": config.base_url,
            "model": config.model, "connectTimeoutSeconds": config.timeout_seconds,
            "systemPrompt": config.system_prompt,
            "networkMode": config.network_mode,
        }
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(self.local_path)

    def _client(self) -> OpenAICompatibleLLM:
        with self._lock:
            if not self._config:
                raise LLMUnavailable("AI provider is not configured")
            return OpenAICompatibleLLM(
                base_url=self._config.base_url, model=self._config.model,
                api_key=self._api_key,
                system_prompt=self._config.system_prompt or OpenAICompatibleLLM.__dataclass_fields__["system_prompt"].default,
                timeout_seconds=self._config.timeout_seconds,
                network_mode=self._config.network_mode,
            )

    def complete(self, messages: list[dict[str, Any]]) -> str:
        return self._client().complete(messages)

    def complete_with_details(self, messages: list[dict[str, Any]]) -> tuple[str, dict[str, int | str | None] | None]:
        return self._client().complete_with_details(messages)

    def stream(self, messages: list[dict[str, Any]],
               cancel_event: Event | None = None) -> Iterator[str]:
        return self._client().stream(messages, cancel_event)

    def stream_events(self, messages: list[dict[str, Any]],
                      cancel_event: Event | None = None) -> Iterator[dict[str, Any]]:
        return self._client().stream_events(messages, cancel_event)

    def discover_models(self, draft: ModelConfig | None = None, api_key: str | None = None) -> list[str]:
        models, _ = self._discover_models(draft, api_key)
        return models

    def _discover_models(self, draft: ModelConfig | None = None,
                         api_key: str | None = None) -> tuple[list[str], dict[str, Any]]:
        if draft is None:
            client = self._client()
        else:
            with self._lock:
                key = self._api_key if api_key is None or not api_key.strip() else api_key.strip()
            client = OpenAICompatibleLLM(
                base_url=draft.base_url, model=draft.model, api_key=key,
                system_prompt=draft.system_prompt or OpenAICompatibleLLM.__dataclass_fields__["system_prompt"].default,
                timeout_seconds=draft.timeout_seconds,
                network_mode=draft.network_mode,
            )
        headers = {"Accept": "application/json"}
        if client.api_key:
            headers["Authorization"] = f"Bearer {client.api_key}"
        attempts: list[dict[str, Any]] = []
        route_attempts = 2 if client.network_mode == "auto" else 1
        last_error: httpx.RequestError | None = None
        for attempt in range(1, route_attempts + 1):
            trust_env, route = client.network_route(attempt)
            started = perf_counter()
            try:
                discovery_timeout = min(client.timeout_seconds, 30.0)
                with httpx.Client(timeout=httpx.Timeout(discovery_timeout),
                                  trust_env=trust_env) as http:
                    response = http.get(client.base_url.rstrip("/") + "/models", headers=headers)
                    response.raise_for_status()
                    data = response.json()
                if not isinstance(data, dict):
                    raise TypeError("model list response must be an object")
                values = data.get("data", [])
                if not isinstance(values, list):
                    raise TypeError("model list data must be an array")
                models = sorted({item["id"] for item in values
                                 if isinstance(item, dict) and isinstance(item.get("id"), str)})
                attempts.append({"route": route, "outcome": "connected",
                                 "durationMs": round((perf_counter() - started) * 1000)})
                return models, {"requestedMode": client.network_mode, "routeUsed": route,
                                "attempts": attempts}
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                attempts.append({"route": route, "outcome": "http-error", "httpStatus": status,
                                 "durationMs": round((perf_counter() - started) * 1000)})
                diagnostics = {"requestedMode": client.network_mode, "routeUsed": route,
                               "attempts": attempts}
                if status in {401, 403}:
                    raise ModelSettingsError(
                        "modelDiscoveryUnauthorized", "Model provider rejected the API key", 401,
                        diagnostics,
                    ) from exc
                if status in {404, 405}:
                    raise ModelSettingsError(
                        "modelDiscoveryUnsupported",
                        "Model provider does not expose a compatible /models endpoint", 502,
                        diagnostics,
                    ) from exc
                raise ModelSettingsError(
                    "modelDiscoveryHttpError", f"Model provider returned HTTP {status}", 502,
                    diagnostics,
                ) from exc
            except httpx.RequestError as exc:
                category = _connection_category(exc)
                attempts.append({"route": route, "outcome": "connection-error",
                                 "category": category,
                                 "durationMs": round((perf_counter() - started) * 1000)})
                last_error = exc
                if (attempt < route_attempts
                        and _connection_establishment_failed(exc)):
                    continue
                break
            except (ValueError, TypeError) as exc:
                attempts.append({"route": route, "outcome": "invalid-response",
                                 "durationMs": round((perf_counter() - started) * 1000)})
                raise ModelSettingsError(
                    "modelDiscoveryInvalidResponse",
                    "Model provider returned an invalid model list", 502,
                    {"requestedMode": client.network_mode, "routeUsed": route,
                     "attempts": attempts},
                ) from exc
        assert last_error is not None
        category = _connection_category(last_error)
        codes = {
            "timeout": ("modelDiscoveryTimeout", "Model provider connection timed out", 504),
            "dns": ("modelDiscoveryDnsFailed", "Model provider hostname could not be resolved", 503),
            "tls": ("modelDiscoveryTlsFailed", "Model provider TLS handshake failed", 503),
            "proxy": ("modelDiscoveryProxyFailed", "System proxy could not reach the model provider", 503),
        }
        code, message, status = codes.get(
            category,
            ("modelDiscoveryConnectionFailed", "Unable to reach the model provider", 503),
        )
        raise ModelSettingsError(
            code, message, status,
            {"requestedMode": client.network_mode, "routeUsed": None, "attempts": attempts},
        ) from last_error

    def validate_connection(self, *, base_url: str, model: str, api_key: str | None = None,
                            connect_timeout_seconds: float = 15.0, system_prompt: str = "",
                            network_mode: NetworkMode = "auto") -> dict[str, Any]:
        selected = model.strip()
        if len(selected) > 200:
            raise ValueError("model must be at most 200 characters")
        draft = ModelConfig(validate_base_url(base_url), selected,
                            _timeout(connect_timeout_seconds), _prompt(system_prompt),
                            _network_mode(network_mode))
        models, diagnostics = self._discover_models(draft, api_key)
        return {"ok": True, "modelCount": len(models), "selectedModelAvailable": bool(selected) and selected in models,
                "models": models, "diagnostics": diagnostics}


def _connection_category(exc: BaseException) -> str:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.ProxyError):
            return "proxy"
        if isinstance(current, (ssl.SSLError,)):
            return "tls"
        if isinstance(current, socket.gaierror):
            return "dns"
        current = current.__cause__ or current.__context__
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return "connection"


def _connection_establishment_failed(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                            httpx.ProxyError))


def _timeout(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("connectTimeoutSeconds must be a number") from exc
    if not 1 <= result <= 60:
        raise ValueError("connectTimeoutSeconds must be between 1 and 60")
    return result


def _network_mode(value: Any) -> NetworkMode:
    if value not in {"auto", "system", "direct"}:
        raise ValueError("networkMode must be auto, system, or direct")
    return value


def _model(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError("model must be 1-200 characters")
    return value


def _prompt(value: str) -> str:
    if len(value) > 20_000:
        raise ValueError("systemPrompt is too long")
    return value
