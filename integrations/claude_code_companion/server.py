from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


CONTRACT_VERSION = 1
HOST_KIND = "claude-code"
SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,80}$")
MAX_BODY = 1024 * 1024
SERVER: ThreadingHTTPServer | None = None
TOKEN = secrets.token_hex(32)
CLAUDE_BIN = os.getenv("WEAVEPATH_CLAUDE_BIN") or shutil.which("claude")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _config_root() -> Path:
    return Path(os.getenv("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")).resolve(strict=False)


def _discovery_path() -> Path:
    configured = os.getenv("WEAVEPATH_HOST_BRIDGE_DISCOVERY")
    if configured:
        return Path(configured).resolve(strict=False)
    local = os.getenv("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".local" / "share"
    return (base / "WeavePath" / "host-bridge.json").resolve(strict=False)


def _capabilities() -> dict[str, Any]:
    available = bool(CLAUDE_BIN)
    return {
        "canFork": available,
        "canForkFromCheckpoint": False,
        "canNavigate": available and os.name == "nt",
        "canReadTranscript": True,
        "canReadLocalTurns": True,
        "canArchive": False,
        "canRename": False,
        "canOpenExternalWindow": available and os.name == "nt",
        "supportedCheckpointCursorKinds": ["instanceHead"],
    }


def _binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("host binding must be an object")
    for name in ("workflowId", "instanceId", "threadId"):
        if not isinstance(value.get(name), str) or not value[name].strip():
            raise ValueError(f"host binding is missing {name}")
    if not SESSION_ID.fullmatch(value["threadId"]):
        raise ValueError("Claude session ID has an invalid format")
    return value


def _session_files() -> list[Path]:
    projects = _config_root() / "projects"
    if not projects.exists():
        return []
    return sorted(
        (item for item in projects.glob("**/*.jsonl") if item.is_file() and SESSION_ID.fullmatch(item.stem)),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _session_file(session_id: str) -> Path:
    if not SESSION_ID.fullmatch(session_id):
        raise ValueError("Claude session ID has an invalid format")
    matches = [item for item in _session_files() if item.stem == session_id]
    if len(matches) != 1:
        raise FileNotFoundError("Claude session transcript was not found or is ambiguous")
    return matches[0]


def _find_string(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for child in value.values():
            found = _find_string(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_string(child, keys)
            if found:
                return found
    return None


def _run_claude(arguments: list[str]) -> dict[str, Any]:
    if not CLAUDE_BIN:
        raise RuntimeError("Claude Code CLI was not found")
    completed = subprocess.run(
        [CLAUDE_BIN, *arguments], check=False, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Claude Code failed").strip())
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Claude Code returned non-JSON output") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Claude Code returned an invalid result")
    return value


def execute(operation: str, payload: dict[str, Any], operation_id: str | None = None) -> dict[str, Any]:
    if operation == "resolveCurrentContext":
        context = payload.get("requestContext") if isinstance(payload.get("requestContext"), dict) else {}
        return {
            "workflowId": context.get("workflowId"), "instanceId": context.get("instanceId"),
            "memoryRoute": context.get("memoryRoute") if isinstance(context.get("memoryRoute"), list) else [],
            "metadata": {},
        }
    if operation == "listConversations":
        items = [{
            "threadId": item.stem, "providerConversationId": item.stem,
            "title": item.stem,
            "updatedAt": datetime.fromtimestamp(item.stat().st_mtime, timezone.utc).isoformat(),
            "metadata": {"transcriptPath": str(item)},
        } for item in _session_files()[:100]]
        return {"items": items, "nextCursor": None}

    source = _binding(payload.get("source") if operation == "fork" else payload.get("binding"))
    if operation == "fork":
        checkpoint = payload.get("checkpoint")
        if isinstance(checkpoint, dict) and checkpoint.get("kind") not in {None, "instanceHead"}:
            raise ValueError("Claude Code supports forking a resumed session head, not an arbitrary transcript turn")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Claude Code session forks require a first prompt")
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        target = options.get("targetInstanceId")
        if not isinstance(target, str) or not target:
            raise ValueError("targetInstanceId is required")
        value = _run_claude([
            "--print", "--resume", source["threadId"], "--fork-session",
            "--output-format", "json", prompt.strip(),
        ])
        child_id = _find_string(value, {"session_id", "sessionId"})
        if not child_id or not SESSION_ID.fullmatch(child_id) or child_id == source["threadId"]:
            raise RuntimeError("Claude Code did not return a distinct forked session ID")
        return {"binding": {
            "workflowId": source["workflowId"], "instanceId": target,
            "threadId": child_id, "provider": HOST_KIND,
            "providerConversationId": child_id,
            "metadata": {"operationId": operation_id},
        }}
    if operation == "inspect":
        transcript = _session_file(source["threadId"])
        offset = max(0, int(payload.get("cursor") or 0))
        limit = min(200, max(1, int(payload.get("limit") or 50)))
        items: list[dict[str, Any]] = []
        with transcript.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index < offset:
                    continue
                if len(items) >= limit:
                    break
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        items.append(value)
                except json.JSONDecodeError:
                    items.append({"type": "unparseable", "line": index + 1})
        next_cursor = str(offset + len(items)) if len(items) == limit else None
        return {"items": items, "nextCursor": next_cursor}
    if operation == "navigate":
        if not CLAUDE_BIN or os.name != "nt":
            return {"ok": False, "code": "hostCapabilityUnsupported", "message": "Opening a Claude terminal is supported only on Windows"}
        subprocess.Popen(
            ["cmd.exe", "/d", "/c", "start", "", CLAUDE_BIN, "--resume", source["threadId"]],
            close_fds=True,
        )
        return {"ok": True, "data": {"opened": True}}
    return {"ok": False, "code": "hostCapabilityUnsupported", "message": f"Claude Code companion does not support {operation}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "WeavePathClaudeCompanion/0.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {TOKEN}")

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"code": "hostUnauthorized"})
            return
        if self.path != "/v1/handshake":
            self._send(404, {"code": "notFound"})
            return
        limitations = [
            "Claude Code exposes session-head forks via --resume --fork-session; arbitrary historical-turn forks are unavailable.",
            "Claude Code has no supported archive or rename CLI operation.",
        ]
        if not CLAUDE_BIN:
            limitations.insert(0, "Claude Code CLI was not found on PATH.")
        self._send(200, {
            "contractVersion": CONTRACT_VERSION, "hostKind": HOST_KIND,
            "connected": True, "capabilities": _capabilities(), "limitations": limitations,
        })

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"code": "hostUnauthorized"})
            return
        matched = re.fullmatch(r"/v1/operations/([A-Za-z]+)", self.path)
        if not matched:
            self._send(404, {"code": "notFound"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY:
                raise ValueError("request body is too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("contractVersion") != CONTRACT_VERSION:
                self._send(409, {"code": "hostContractMismatch"})
                return
            result = execute(matched.group(1), body.get("payload") or {}, body.get("operationId"))
            self._send(200, {"contractVersion": CONTRACT_VERSION, "hostKind": HOST_KIND, "result": result})
        except Exception as exc:
            self._send(502, {
                "contractVersion": CONTRACT_VERSION, "hostKind": HOST_KIND,
                "error": {"code": "hostOperationFailed", "message": str(exc)},
            })


def _write_discovery(base_url: str) -> None:
    target = _discovery_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({
        "contractVersion": CONTRACT_VERSION, "hostKind": HOST_KIND,
        "baseUrl": base_url, "token": TOKEN, "pid": os.getpid(), "updatedAt": _now(),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _cleanup() -> None:
    target = _discovery_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        if value.get("pid") == os.getpid():
            target.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="WeavePath Claude Code host companion")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    global SERVER
    SERVER = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    _write_discovery(f"http://127.0.0.1:{SERVER.server_port}")

    def stop(_signum: int, _frame: object) -> None:
        _cleanup()
        if SERVER:
            threading.Thread(target=SERVER.shutdown, daemon=True).start()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        SERVER.serve_forever(poll_interval=0.25)
    finally:
        _cleanup()
        SERVER.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
