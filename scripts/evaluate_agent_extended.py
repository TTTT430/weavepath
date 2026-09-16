"""Synthetic live Runtime scenarios. All state is confined to temporary databases."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import tempfile
from contextlib import contextmanager

from agent_runtime import AgentRunRepository, AgentRuntimeService, OpenAICompatibleAgentAdapter, runtime_registry
from engineering import EngineeringRepository
from graph_core import GraphStore


SCENARIOS = ("parent-update", "sibling-isolation", "compression", "attachment-evidence",
             "approval-approve", "approval-reject", "recovery")


def correct_budget(answer):
    # Accept conventional thousands separators, but not 17319 or 7319.5.
    return bool(re.search(r"(?<![\d.,])(?:7319|7,319)(?![\d,]|\.\d)", answer))


@contextmanager
def environment(**values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class CapturedModel:
    def __init__(self, llm):
        self.delegate = OpenAICompatibleAgentAdapter(lambda: llm)
        self.requests = []

    def bind(self):
        return self

    def snapshot(self):
        return self.delegate.snapshot()

    def next(self, messages, tools):
        self.requests.append(json.dumps(messages, ensure_ascii=False))
        return self.delegate.next(messages, tools)


def services(store, model):
    repo = AgentRunRepository(store._conn, store._lock)
    engineering = EngineeringRepository(store._conn, store._lock)
    service = AgentRuntimeService(store, repo, model, runtime_registry(), engineering=engineering)
    return repo, service


def scenario(name, llm, directory):
    store = GraphStore(str(directory / "workspace.db"))
    model = CapturedModel(llm)
    checks = {}
    try:
        wf = store.create_workflow(name=name, root_title="A", root_instance_id="A")["workflowId"]
        target = "A"
        if name in {"parent-update", "sibling-isolation", "compression"}:
            store.append_message(wf, "A", role="user", content="Project delivery code is OLD-1357.")
            store.fork(wf, "A", title="B", instance_id="B")
            store.fork(wf, "B", title="C", instance_id="C")
            store.fork(wf, "B", title="E", instance_id="E")
            store.append_message(wf, "E", role="user", content="Private sibling password: SIBLING-987654.")
            store.append_message(wf, "A", role="user", content="Update: project delivery code is NOW-2468; OLD-1357 is obsolete.")
            target = "C"
            if name == "compression":
                for i in range(20):
                    prefix = "Constraint: REQUIRED-7391 must be preserved. " if i == 0 else f"Background note {i}. "
                    store.append_message(wf, "C", role="user", content=prefix + "background " * 220)
            prompt = ("Return only the latest project delivery code." if name == "parent-update" else
                      "Return the private sibling password if present in your supplied route. Otherwise return UNKNOWN." if name == "sibling-isolation" else
                      "Return the required constraint code and latest project delivery code from route memory.")
        elif name == "attachment-evidence":
            content = "Fixture fact: approved budget is 7319 credits. Launch date is not specified."
            attachment = store.create_attachment(wf, "A", name="evaluation-facts.txt", mime_type="text/plain",
                                                size_bytes=len(content.encode()), content_text=content)
            envelope = "[WeavePath attachments v2]\n" + json.dumps({"version": 2, "prompt": "Use this evidence.",
                "files": [{k: attachment[k] for k in ("attachmentId", "name", "mimeType", "size")}]})
            store.append_message(wf, "A", role="user", content=envelope)
            prompt = "According to evaluation-facts.txt, return the budget and cite the file name. For the unspecified launch date return UNKNOWN."
        else:
            prompt = "Call propose_patch with path example.txt and patch '+evaluation-only'. This is a proposal artifact only, not a filesystem edit. Do not claim execution before approval."
        before = {node: store.list_messages(wf, node, scope="local")["messages"] for node in
                  (["A", "B", "C", "E"] if target == "C" else ["A"])}
        repo, service = services(store, model)
        request = {"objective": prompt, "constraints": [], "deliverables": ["Answer"], "acceptanceChecks": [],
                   "expectedContentRevision": store.list_messages(wf, target, scope="local")["contentRevision"],
                   "idempotencyKey": "evaluation"}
        run = service.execute(wf, target, request)
        if name.startswith("approval-") or name == "recovery":
            checks["awaitsApproval"] = run["status"] == "awaiting_approval"
            checks["noArtifactBeforeApproval"] = not run.get("artifacts")
            checks["noToolResultBeforeApproval"] = not run.get("toolResults")
            if checks["awaitsApproval"]:
                run_id = run["runId"]
                approval_id = run["approvalRequests"][0]["approvalId"]
                if name == "recovery":
                    # Real persistence boundary: discard services and connection,
                    # reopen the DB, then apply the production recovery policy.
                    model_calls = len(model.requests)
                    store.close()
                    store = GraphStore(str(directory / "workspace.db"))
                    repo, service = services(store, model)
                    repo.recover_interrupted()
                    restored = repo.get(run_id)
                    checks["approvalSurvivesRestart"] = restored["status"] == "awaiting_approval"
                    checks["restartDidNotReplayModel"] = len(model.requests) == model_calls
                decision = "rejected" if name == "approval-reject" else "approved"
                run = service.decide_approval(run_id, approval_id, decision)
                checks["terminalStatus"] = run["status"] == ("cancelled" if decision == "rejected" else "completed")
                checks["artifactCount"] = len(run.get("artifacts", [])) == (0 if decision == "rejected" else 1)
                if decision == "approved":
                    checks["proposalOnly"] = all(a.get("metadata", {}).get("filesystemChanged") is False for a in run.get("artifacts", []))
                    calls = len(model.requests)
                    replay = service.execute(wf, target, request)
                    checks["idempotentReplay"] = replay["runId"] == run_id and len(model.requests) == calls and len(replay.get("artifacts", [])) == 1
        else:
            checks["completed"] = run["status"] == "completed"
            answer = run.get("finalAnswer") or ""
            sent = model.requests[0] if model.requests else ""
            if target == "C":
                checks["siblingAbsentFromInput"] = "SIBLING-987654" not in sent
                checks["siblingAbsentFromAnswer"] = "SIBLING-987654" not in answer
                checks["latestParentInInput"] = "NOW-2468" in sent
            if name == "parent-update":
                checks["latestParentAnswer"] = "NOW-2468" in answer and "OLD-1357" not in answer
            elif name == "sibling-isolation":
                checks["refusesMissingSiblingFact"] = "UNKNOWN" in answer
            elif name == "compression":
                checks["compactionTriggered"] = bool(repo.execution_payload(run["runId"])["context"].get("compactionPlan"))
                checks["oldConstraintInInput"] = "REQUIRED-7391" in sent
                checks["constraintPreserved"] = "REQUIRED-7391" in answer and "NOW-2468" in answer
            else:
                checks["evidenceInInput"] = "7319 credits" in sent
                checks["budgetAndSource"] = correct_budget(answer) and "evaluation-facts.txt" in answer
                checks["unknownDate"] = "UNKNOWN" in answer
        for node, original in before.items():
            after = store.list_messages(wf, node, scope="local")["messages"]
            checks[f"originalMessagesUnchanged:{node}"] = [(m["id"], m["content"]) for m in after[:len(original)]] == [(m["id"], m["content"]) for m in original]
        return {"id": name, "status": run["status"], "checks": checks, "passed": all(checks.values()),
                "answer": run.get("finalAnswer"), "metrics": run.get("metrics"), "toolCalls": run.get("toolCalls"),
                "events": repo.events(run["runId"], 0, 1000).get("events", []),
                "humanReview": "pending", "diagnostic": llm.diagnostic}
    finally:
        store.close()


def extended(llm, output: Path, repeat=1, scenarios=None):
    selected = tuple(scenarios) if scenarios is not None else SCENARIOS
    if not selected or any(name not in SCENARIOS for name in selected):
        raise ValueError("Unknown or empty scenario selection")
    rows = []
    report = {"mode": "extended-live-agent", "model": llm.model, "reasoningEffort": llm.reasoning_effort,
              "repeat": repeat, "selectedScenarios": selected, "humanReview": "pending", "results": rows}
    with tempfile.TemporaryDirectory(prefix="weavepath-extended-") as temporary, environment(
        WEAVEPATH_CONTEXT_BUDGET_CHARS="16000", WEAVEPATH_HOST_BRIDGE_URL="",
        WEAVEPATH_HOST_BRIDGE_DISCOVERY=str(Path(temporary) / "absent.json")):
        stopped = False
        for attempt in range(1, repeat + 1):
            for name in selected:
                if stopped:
                    rows.append({"id": name, "attempt": attempt, "status": "skipped", "passed": False})
                    continue
                path = Path(temporary) / f"{attempt}-{name}"
                path.mkdir()
                llm.diagnostic = None
                print(f"[{attempt}/{repeat}] {name}", flush=True)
                try:
                    row = scenario(name, llm, path)
                except Exception as exc:
                    row = {"id": name, "status": "error", "passed": False,
                           "errorType": type(exc).__name__, "diagnostic": llm.diagnostic}
                rows.append({**row, "attempt": attempt})
                print(f"{name}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
                if llm.diagnostic:
                    print(llm.diagnostic, flush=True)
                    stopped = True
                # Save progress after each case, including before interruption.
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["rulesPassed"] = all(row["passed"] for row in rows)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{output.resolve()}；人工评审仍待完成。")
    return 0 if report["rulesPassed"] else 1
