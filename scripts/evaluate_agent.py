"""Offline acceptance suite and opt-in live Agent evaluation. No app DB access."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from api.llm import OpenAICompatibleLLM
import httpx


class DiagnosticLLM(OpenAICompatibleLLM):
    """Record only allowlisted metadata, never upstream bodies or credentials."""
    diagnostic: dict | None = None

    def _transport_error(self, exc):
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        hints = {
            400: "请求被拒绝：检查模型是否支持工具调用及 reasoning_effort 参数。",
            401: "认证失败：请在当前 PowerShell 重新输入该服务商的有效 API 密钥。",
            403: "访问被拒绝：检查密钥的模型权限、账户状态或服务端访问限制。",
            404: "地址或模型不存在：检查 BASE_URL 是否以 /v1 结尾，以及模型 ID。",
            402: "服务商要求付费：检查账户余额。",
            422: "参数不兼容：检查工具调用和思考强度支持情况。",
            429: "限流或额度不足：检查服务商配额，稍后重试。",
        }
        error = super()._transport_error(exc)
        self.diagnostic = {"httpStatus": status, "errorCode": error.code,
                           "hint": hints.get(status, "服务商或网络异常；检查服务状态与连接配置。")}
        return error


def offline(output: Path) -> int:
    cases = json.loads((ROOT / "evals/scenarios.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="weavepath-eval-") as temporary:
        xml = Path(temporary) / "results.xml"
        env = {**os.environ, "WEAVEPATH_HOST_BRIDGE_DISCOVERY": str(Path(temporary) / "absent.json"),
               "WEAVEPATH_HOST_BRIDGE_URL": ""}
        result = subprocess.run([sys.executable, "-m", "pytest", *[c["node"] for c in cases],
                                 "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"], cwd=ROOT, env=env)
        rows = []
        if xml.exists():
            for test in ET.parse(xml).iter("testcase"):
                status = "failed" if test.find("failure") is not None or test.find("error") is not None else "skipped" if test.find("skipped") is not None else "passed"
                rows.append({"name": test.get("name"), "status": status, "seconds": test.get("time")})
        passed = result.returncode == 0 and len(rows) == len(cases) and all(r["status"] == "passed" for r in rows)
        report = {"mode": "offline-rule-regression", "passed": passed, "expectedScenarios": len(cases),
                  "note": "Scripted tests, not real-model task success or UI performance.", "results": rows}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if passed else 1


def live(output: Path) -> int:
    from api.llm import OpenAICompatibleLLM
    from api.app import create_app
    from api.model_settings import RuntimeModelSettings
    from graph_core import GraphStore
    from fastapi.testclient import TestClient
    base, model, key = (os.getenv("EVAL_BASE_URL"), os.getenv("EVAL_MODEL"), os.getenv("EVAL_API_KEY"))
    if not all((base, model, key)):
        raise SystemExit("Live evaluation requires EVAL_BASE_URL, EVAL_MODEL and EVAL_API_KEY; no saved app credentials are read.")
    llm = DiagnosticLLM(base_url=base.strip().rstrip("/"), model=model.strip(), api_key=key.strip(),
                              reasoning_effort=os.getenv("EVAL_REASONING_EFFORT") or None)
    cases = [
        ("calculator", "Use safe_calculator to calculate 47 * 128. Return the result.", "6016"),
        ("missing-evidence", "What is the internal project launch date? No launch date or sources have been provided. Do not invent one.", None),
        ("focused-answer", "用中文简要解释 BM25 和向量检索的区别，不需要代码。", None),
    ]
    rows = []
    with tempfile.TemporaryDirectory(prefix="weavepath-live-eval-") as temporary:
        os.environ["WEAVEPATH_HOST_BRIDGE_DISCOVERY"] = str(Path(temporary) / "absent.json")
        os.environ["WEAVEPATH_HOST_BRIDGE_URL"] = ""
        store = GraphStore(":memory:")
        try:
            settings = RuntimeModelSettings(Path(temporary) / "settings.json", env={})
            with TestClient(create_app(store, llm_client=llm, model_settings=settings, background_agent_runs=False)) as client:
                for name, prompt, expected in cases:
                    llm.diagnostic = None
                    print(f"正在测评：{name}", flush=True)
                    graph = client.post("/api/v1/workflows", json={"name": name, "rootTitle": "Evaluation", "rootInstanceId": name}).json()
                    response = client.post(f"/api/v1/workflows/{graph['workflowId']}/instances/{name}/runs", json={
                        "objective": prompt, "constraints": [], "deliverables": ["Answer"],
                        "acceptanceChecks": [], "expectedContentRevision": 0, "idempotencyKey": name})
                    run = response.json()
                    if "runId" not in run:
                        rows.append({"id": name, "status": "request_failed", "httpStatus": response.status_code})
                        continue
                    detail = client.get(f"/api/v1/runs/{run['runId']}").json()
                    answer = detail.get("finalAnswer") or ""
                    calls = detail.get("toolCalls", [])
                    rule = None if expected is None else expected in answer and any(c.get("toolName") == "safe_calculator" for c in calls)
                    rows.append({"id": name, "status": detail.get("status"), "answer": answer,
                                 "errorCode": detail.get("errorCode"), "diagnostic": llm.diagnostic,
                                 "characters": len(answer), "rulePassed": rule, "humanReview": "pending",
                                 "metrics": detail.get("metrics"), "toolCalls": calls,
                                 "events": client.get(f"/api/v1/runs/{run['runId']}/events").json().get("events", [])})
                    print(f"{name}: {detail.get('status')}，正文 {len(answer)} 字符", flush=True)
                    if llm.diagnostic:
                        print(f"HTTP {llm.diagnostic['httpStatus']}：{llm.diagnostic['hint']}", flush=True)
                        # A shared connection/config failure cannot assess quality.
                        # Do not spend two more requests on the same failing setup.
                        for skipped, _, _ in cases[len(rows):]:
                            rows.append({"id": skipped, "status": "skipped", "reason": "provider_failure"})
                        break
        finally:
            store.close()
    output.write_text(json.dumps({"mode": "live-agent", "model": model,
        "note": "Human review required; no aggregate quality pass is inferred. Missing provider usage/cost is unavailable.",
        "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已保存：{output.resolve()}")
    print("执行完成，仍需人工评审。" if all(r["status"] == "completed" for r in rows)
          else "测评未通过：存在失败或跳过，请查看上方诊断；这不是模型质量评分。")
    return 0 if all(r["status"] == "completed" and r.get("rulePassed") is not False for r in rows) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicit opt-in: sends synthetic tasks to your provider and incurs charges")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/report.local.json")
    parser.add_argument("--extended", action="store_true", help="Run the seven extended live scenarios; requires --live")
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--scenarios", nargs="+", help="Extended scenario IDs to run; defaults to all")
    args = parser.parse_args()
    if args.extended:
        if not args.live:
            parser.error("--extended requires --live (paid API calls)")
        from evaluate_agent_extended import extended, SCENARIOS
        if args.scenarios and any(name not in SCENARIOS for name in args.scenarios):
            parser.error("Unknown scenario; choose from: " + ", ".join(SCENARIOS))
        if not all(os.getenv(k) for k in ("EVAL_BASE_URL", "EVAL_MODEL", "EVAL_API_KEY")):
            parser.error("Set EVAL_BASE_URL, EVAL_MODEL and EVAL_API_KEY first")
        client = DiagnosticLLM(base_url=os.environ["EVAL_BASE_URL"].strip().rstrip("/"),
            model=os.environ["EVAL_MODEL"].strip(), api_key=os.environ["EVAL_API_KEY"].strip(),
            reasoning_effort=os.getenv("EVAL_REASONING_EFFORT") or None)
        sys.exit(extended(client, args.output, args.repeat, args.scenarios))
    if args.scenarios:
        parser.error("--scenarios requires --extended --live")
    sys.exit(live(args.output) if args.live else offline(args.output))
