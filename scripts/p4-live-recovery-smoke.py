from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import uvicorn

from api.app import create_app
from api.llm import OpenAICompatibleLLM
from graph_core import GraphStore


class RecoveringProvider(BaseHTTPRequestHandler):
    attempts = 0

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        if request.get("model") != "weavepath-live-model" or request.get("stream") is not True:
            self.send_error(422)
            return
        type(self).attempts += 1
        if type(self).attempts == 1:
            partial = b'data: {"choices":[{"delta":{"content":"discard me"}}]}\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(partial) + 4096))
            self.end_headers()
            self.wfile.write(partial)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        complete = (
            'data: {"choices":[{"delta":{"content":"recovered final"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":20,"completion_tokens":2,'
            '"prompt_tokens_details":{"cached_tokens":12}}}\n\n'
            'data: [DONE]\n\n'
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(complete)))
        self.end_headers()
        self.wfile.write(complete)


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def main() -> int:
    provider = ThreadingHTTPServer(("127.0.0.1", 0), RecoveringProvider)
    provider_worker = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_worker.start()
    api_server: uvicorn.Server | None = None
    api_worker: threading.Thread | None = None
    with tempfile.TemporaryDirectory(prefix="weavepath-p4-live-") as temporary:
        store = GraphStore(Path(temporary) / "workspace.db")
        llm = OpenAICompatibleLLM(
            base_url=f"http://127.0.0.1:{provider.server_port}/v1",
            model="weavepath-live-model", api_key="test-only", network_mode="direct",
        )
        api_server = uvicorn.Server(uvicorn.Config(
            create_app(store, llm), host="127.0.0.1", port=_free_port(),
            log_level="error", access_log=False,
        ))
        api_worker = threading.Thread(target=api_server.run, daemon=True)
        api_worker.start()
        try:
            for _ in range(200):
                if api_server.started:
                    break
                time.sleep(0.025)
            if not api_server.started:
                raise RuntimeError("WeavePath API did not start")
            base = f"http://127.0.0.1:{api_server.config.port}/api/v1"
            # The smoke test owns both loopback sockets.  Never let a machine-
            # level HTTP(S)_PROXY route these local requests through Clash or
            # another proxy process.
            with httpx.Client(timeout=20.0, trust_env=False) as client:
                workflow = client.post(base + "/workflows", json={
                    "name": "P4 live", "rootTitle": "A", "rootInstanceId": "A",
                }).raise_for_status().json()
                route = f"{base}/workflows/{workflow['workflowId']}/instances/A"
                response = client.post(route + "/chat/stream", json={
                    "content": "exercise real reconnect", "idempotencyKey": "p4-live",
                })
                response.raise_for_status()
                if "message.reset" not in response.text or "message.completed" not in response.text:
                    raise RuntimeError("Live SSE did not expose reset and completed events")
                recovery = client.get(route + "/chat/p4-live/events").raise_for_status().json()
                event_types = [item["type"] for item in recovery["events"]]
                if recovery["status"] != "completed" or "message.reset" not in event_types:
                    raise RuntimeError("Durable recovery journal is incomplete")
                messages = client.get(route + "/messages?scope=local").raise_for_status().json()["messages"]
                if [item["role"] for item in messages] != ["user", "assistant"]:
                    raise RuntimeError("Recovered request duplicated or lost a message")
                if messages[-1]["content"] != "recovered final":
                    raise RuntimeError("Partial provider output was persisted")
                details = messages[-1]["responseDetails"]
                if details["cachedInputTokens"] != 12 or details["uncachedInputTokens"] != 8:
                    raise RuntimeError("Provider cache usage was not preserved")
            print(
                "P4 live socket recovery passed: partial stream reset, automatic provider "
                "reconnect, durable SSE replay, one user message, one complete answer."
            )
        finally:
            api_server.should_exit = True
            api_worker.join(timeout=5)
            store.close()
    provider.shutdown()
    provider.server_close()
    provider_worker.join(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
