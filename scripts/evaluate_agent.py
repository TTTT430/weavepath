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
    llm = OpenAICompatibleLLM(base_url=base, model=model, api_key=key,
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
                                 "characters": len(answer), "rulePassed": rule, "humanReview": "pending",
                                 "metrics": detail.get("metrics"), "toolCalls": calls,
                                 "events": client.get(f"/api/v1/runs/{run['runId']}/events").json().get("events", [])})
        finally:
            store.close()
    output.write_text(json.dumps({"mode": "live-agent", "model": model,
        "note": "Human review required; no aggregate quality pass is inferred. Missing provider usage/cost is unavailable.",
        "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(r["status"] == "completed" and r.get("rulePassed") is not False for r in rows) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicit opt-in: sends synthetic tasks to your provider and incurs charges")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/report.local.json")
    args = parser.parse_args()
    sys.exit(live(args.output) if args.live else offline(args.output))
