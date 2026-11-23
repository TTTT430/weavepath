"""Paid, opt-in comparison of two Agent Run prompt orders on synthetic data."""

import json
from uuid import uuid4

from agent_runtime import ModelTurn, ScriptedMockAgentAdapter
from agent_runtime.parent_updates import STRATEGY, with_update_guidance
from agent_runtime.service import AgentRunError
from evaluate_agent_extended import CapturedModel, services
from graph_core import GraphStore

OLD = "OLD-1357"
NEW = "NEW-2468"


def arm(llm, scenario, experimental):
    graph = GraphStore(":memory:")
    model = CapturedModel(llm)
    try:
        workflow = graph.create_workflow(
            name="Prefix cache evaluation", root_title="A", root_instance_id="A",
        )["workflowId"]
        graph.append_message(workflow, "A", role="user",
                             content=f"Fixture {uuid4().hex}. Shared design notes. " + "design notes " * 1800)
        graph.fork(workflow, "A", instance_id="B", initial_message=f"Delivery code is {OLD}.")
        for node in ("C", "E"):
            graph.fork(workflow, "B", instance_id=node,
                       initial_message=f"PRIVATE-{node}. " + f"Design option {node}. " * 2400)
        repo, service = services(graph, model)

        def run(target, key, runner=service):
            body = {
                "objective": "Return only the current delivery code, with no explanation.",
                "constraints": [], "deliverables": [], "acceptanceChecks": [],
                "expectedContentRevision": graph.list_messages(workflow, target)["contentRevision"],
                "idempotencyKey": key,
            }
            if experimental:
                body["contextStrategy"] = STRATEGY
            result = runner.execute(workflow, target, body)
            return repo.get(result["runId"])

        if scenario == "same-branch":
            warm = run("C", "warm")
            target = "C"
        else:
            mock = ScriptedMockAgentAdapter([ModelTurn(final_answer=OLD), ModelTurn(final_answer=OLD)])
            _, seed_service = services(graph, mock)
            run("C", "seed-c", seed_service)
            run("E", "seed-e", seed_service)
            target = "E"

        graph.append_message(workflow, "B", role="user", content=f"New rule: use {NEW}; {OLD} is obsolete.")
        if scenario == "switch-branch":
            warm = run("C", "warm")
        measured = run(target, "measure")
        metrics = measured["metrics"]
        cache_reported = metrics["cacheCoverage"] == 1 and metrics["cachedInputTokens"] is not None
        other = "E" if target == "C" else "C"
        return {
            "strategy": STRATEGY if experimental else "routeOrder",
            "warmup": warm["metrics"], "measured": metrics,
            "cacheComparable": cache_reported and measured["compactionPlan"] is None,
            "compactionTriggered": measured["compactionPlan"] is not None,
            "newRuleApplied": (measured["finalAnswer"] or "").strip() == NEW,
            "siblingIsolated": f"PRIVATE-{other}" not in model.requests[-1],
        }
    finally:
        graph.close()


def compare(llm, output):
    llm.system_prompt = with_update_guidance(llm.system_prompt)
    pairs = []
    report = {"model": llm.model, "pairs": pairs}
    for scenario in ("same-branch", "switch-branch"):
        for trial in range(3):
            row = {"scenario": scenario, "trial": trial + 1, "arms": []}
            pairs.append(row)
            try:
                for experimental in ((False, True) if trial % 2 == 0 else (True, False)):
                    row["arms"].append(arm(llm, scenario, experimental))
                baseline, treatment = sorted(row["arms"], key=lambda result: result["strategy"] == STRATEGY)
                row["cachedTokenDelta"] = (
                    treatment["measured"]["cachedInputTokens"] - baseline["measured"]["cachedInputTokens"]
                    if all(result["cacheComparable"] for result in row["arms"]) else None
                )
            except AgentRunError as exc:
                row["errorCode"] = exc.code
                output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                return 1
            output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(not all(result["newRuleApplied"] and result["siblingIsolated"]
                       for row in pairs for result in row["arms"]))
