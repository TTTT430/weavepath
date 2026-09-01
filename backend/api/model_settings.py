from __future__ import annotations

import ipaddress
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from api.llm import LLMUnavailable, OpenAICompatibleLLM


Persistence = Literal["memory", "local"]


class ModelSettingsError(LLMUnavailable):
    def __init__(self, code: str, message: str, status_code: int = 503) -> None:
        super().__init__(message, code=code, status_code=status_code)


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
    timeout_seconds: float = 60.0
    system_prompt: str = ""


class RuntimeModelSettings:
    """Mutable runtime settings. Secrets are process-memory/env only."""

    def __init__(self, local_path: str | Path, env: dict[str, str] | None = None) -> None:
        self.local_path = Path(local_path)
        self._env = dict(os.environ if env is None else env)
        self._lock = threading.RLock()
        self._config: ModelConfig | None = None
        self._api_key = ""
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
                    _timeout(self._env.get("WEAVEPATH_LLM_TIMEOUT")
                             or self._env.get("COTHINKER_LLM_TIMEOUT") or "60"),
                    _prompt(self._env.get("WEAVEPATH_LLM_SYSTEM_PROMPT")
                            or self._env.get("COTHINKER_LLM_SYSTEM_PROMPT") or ""),
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
                _timeout(raw.get("timeoutSeconds", 60)), str(raw.get("systemPrompt", "")),
            )
            self._source, self._persistence = "local", "local"
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
        self._api_key = api_key

    def status(self) -> dict[str, Any]:
        with self._lock:
            config = self._config
            configured = bool(config and config.base_url and config.model)
            return {
                "configured": configured,
                "provider": "openai-compatible",
                "baseUrl": config.base_url if config else None,
                "model": config.model if config else None,
                "timeoutSeconds": config.timeout_seconds if config else 60.0,
                "systemPrompt": config.system_prompt if config else "",
                "hasApiKey": bool(self._api_key),
                "source": self._source,
                "persistence": self._persistence,
                "reason": None if configured else "AI provider is not configured",
            }

    def configure(self, *, base_url: str, model: str, api_key: str | None = None,
                  timeout_seconds: float = 60.0, system_prompt: str = "",
                  persistence: Persistence = "memory", clear_api_key: bool = False) -> dict[str, Any]:
        config = ModelConfig(validate_base_url(base_url), _model(model), _timeout(timeout_seconds), _prompt(system_prompt))
        with self._lock:
            self._config = config
            if clear_api_key:
                self._api_key = ""
            elif api_key is not None and api_key.strip():
                self._api_key = api_key.strip()
            self._source, self._persistence = "runtime", persistence
            if persistence == "local":
                self._write_non_secret(config)
                self._source = "local"
            return self.status()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._config, self._api_key, self._source, self._persistence = None, "", "none", "memory"
            self.local_path.unlink(missing_ok=True)
            self._load()
            return self.status()

    def _write_non_secret(self, config: ModelConfig) -> None:
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.local_path.with_suffix(self.local_path.suffix + ".tmp")
        value = {
            "version": 1, "provider": "openai-compatible", "baseUrl": config.base_url,
            "model": config.model, "timeoutSeconds": config.timeout_seconds,
            "systemPrompt": config.system_prompt,
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
            )

    def complete(self, messages: list[dict[str, Any]]) -> str:
        return self._client().complete(messages)

    def discover_models(self, draft: ModelConfig | None = None, api_key: str | None = None) -> list[str]:
        if draft is None:
            client = self._client()
        else:
            with self._lock:
                key = self._api_key if api_key is None or not api_key.strip() else api_key.strip()
            client = OpenAICompatibleLLM(
                base_url=draft.base_url, model=draft.model, api_key=key,
                system_prompt=draft.system_prompt or OpenAICompatibleLLM.__dataclass_fields__["system_prompt"].default,
                timeout_seconds=draft.timeout_seconds,
            )
        headers = {"Accept": "application/json"}
        if client.api_key:
            headers["Authorization"] = f"Bearer {client.api_key}"
        try:
            with httpx.Client(timeout=min(client.timeout_seconds, 30.0)) as http:
                response = http.get(client.base_url.rstrip("/") + "/models", headers=headers)
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict):
                raise TypeError("model list response must be an object")
            values = data.get("data", [])
            if not isinstance(values, list):
                raise TypeError("model list data must be an array")
            models = sorted({item["id"] for item in values if isinstance(item, dict) and isinstance(item.get("id"), str)})
        except httpx.TimeoutException as exc:
            raise ModelSettingsError(
                "modelDiscoveryTimeout", "Model provider connection timed out", 504
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise ModelSettingsError(
                    "modelDiscoveryUnauthorized",
                    "Model provider rejected the API key",
                    401,
                ) from exc
            if status in {404, 405}:
                raise ModelSettingsError(
                    "modelDiscoveryUnsupported",
                    "Model provider does not expose a compatible /models endpoint",
                    502,
                ) from exc
            raise ModelSettingsError(
                "modelDiscoveryHttpError",
                f"Model provider returned HTTP {status}",
                502,
            ) from exc
        except httpx.RequestError as exc:
            raise ModelSettingsError(
                "modelDiscoveryConnectionFailed", "Unable to reach the model provider", 503
            ) from exc
        except (ValueError, TypeError) as exc:
            raise ModelSettingsError(
                "modelDiscoveryInvalidResponse",
                "Model provider returned an invalid model list",
                502,
            ) from exc
        return models

    def validate_connection(self, *, base_url: str, model: str, api_key: str | None = None,
                            timeout_seconds: float = 60.0, system_prompt: str = "") -> dict[str, Any]:
        selected = model.strip()
        if len(selected) > 200:
            raise ValueError("model must be at most 200 characters")
        draft = ModelConfig(validate_base_url(base_url), selected, _timeout(timeout_seconds), _prompt(system_prompt))
        models = self.discover_models(draft, api_key)
        return {"ok": True, "modelCount": len(models), "selectedModelAvailable": bool(selected) and selected in models,
                "models": models}


def _timeout(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeoutSeconds must be a number") from exc
    if not 1 <= result <= 300:
        raise ValueError("timeoutSeconds must be between 1 and 300")
    return result


def _model(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError("model must be 1-200 characters")
    return value


def _prompt(value: str) -> str:
    if len(value) > 20_000:
        raise ValueError("systemPrompt is too long")
    return value
