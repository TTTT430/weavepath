from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _request(url: str, token: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="weavepath-claude-live-") as temporary:
        discovery = Path(temporary) / "host-bridge.json"
        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "WEAVEPATH_HOST_BRIDGE_DISCOVERY": str(discovery),
        }
        companion = subprocess.Popen(
            [sys.executable, "-m", "integrations.claude_code_companion.server"],
            cwd=ROOT, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True,
        )
        try:
            configuration = None
            for _ in range(100):
                try:
                    configuration = json.loads(discovery.read_text(encoding="utf-8"))
                    break
                except (OSError, ValueError):
                    time.sleep(0.025)
            if not isinstance(configuration, dict):
                raise RuntimeError("Claude companion did not publish discovery information")
            base_url = str(configuration["baseUrl"])
            token = str(configuration["token"])
            descriptor = _request(base_url + "/v1/handshake", token)
            if descriptor.get("contractVersion") != 1 or descriptor.get("hostKind") != "claude-code":
                raise RuntimeError("Claude companion handshake identity is invalid")
            listed = _request(base_url + "/v1/operations/listConversations", token, body={
                "contractVersion": 1, "operationId": "live-list", "payload": {},
            })
            items = listed.get("result", {}).get("items", [])
            if not isinstance(items, list):
                raise RuntimeError("Claude companion returned an invalid task list")
            if items:
                session_id = items[0].get("threadId")
                inspected = _request(base_url + "/v1/operations/inspect", token, body={
                    "contractVersion": 1, "operationId": "live-inspect",
                    "payload": {
                        "binding": {
                            "workflowId": "weavepath-live-check", "instanceId": "current",
                            "threadId": session_id, "provider": "claude-code",
                            "providerConversationId": session_id,
                        },
                        "limit": 1,
                    },
                })
                if not isinstance(inspected.get("result", {}).get("items"), list):
                    raise RuntimeError("Claude companion returned an invalid transcript page")
            print(
                "Claude Code live read-only bridge check passed "
                f"({len(items)} local sessions; CLI capability={descriptor['capabilities']['canFork']})."
            )
        finally:
            companion.terminate()
            try:
                companion.wait(timeout=3)
            except subprocess.TimeoutExpired:
                companion.kill()
                companion.wait(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
