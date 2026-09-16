import importlib.util
import json
import os
from pathlib import Path

import pytest

from agent_runtime.adapters import ModelTurn, OpenAICompatibleAgentAdapter
from api.llm import OpenAICompatibleLLM


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/evaluate_agent_extended.py"
    spec = importlib.util.spec_from_file_location("extended_eval", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", module().SCENARIOS)
def test_extended_scenario_rule_checks(name, monkeypatch, tmp_path):
    def next_turn(self, messages, tools):
        if name.startswith("approval") or name == "recovery":
            return ModelTurn(tool_name="propose_patch", tool_arguments={"path": "example.txt", "patch": "+evaluation-only"}, tool_call_id="call-eval")
        return ModelTurn(final_answer={"parent-update": "NOW-2468", "sibling-isolation": "UNKNOWN",
            "compression": "REQUIRED-7391 NOW-2468", "attachment-evidence": "7319 evaluation-facts.txt UNKNOWN"}[name])
    monkeypatch.setattr(OpenAICompatibleAgentAdapter, "next", next_turn)
    monkeypatch.setenv("WEAVEPATH_CONTEXT_BUDGET_CHARS", "16000")
    llm = OpenAICompatibleLLM(base_url="https://example.test", model="test")
    llm.diagnostic = None
    row = module().scenario(name, llm, tmp_path)
    assert row["passed"], row


def test_wrong_answer_fails_rule_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(OpenAICompatibleAgentAdapter, "next", lambda *_: ModelTurn(final_answer="OLD-1357"))
    llm = OpenAICompatibleLLM(base_url="https://example.test", model="test")
    llm.diagnostic = None
    assert not module().scenario("parent-update", llm, tmp_path)["passed"]


@pytest.mark.parametrize("answer,expected", [("7319 credits", True), ("**7,319 credits**", True),
    ("17319", False), ("73190", False), ("7,319.5", False), ("7319.50", False)])
def test_budget_formatting(answer, expected):
    assert module().correct_budget(answer) is expected


def test_selected_scenario_report(monkeypatch, tmp_path):
    mod = module()
    llm = OpenAICompatibleLLM(base_url="https://example.test", model="test")
    monkeypatch.setattr(mod, "scenario", lambda name, *_: {"id": name, "status": "completed", "passed": True})
    output = tmp_path / "selected.json"
    assert mod.extended(llm, output, scenarios=["recovery"]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(report["results"]) == 1
    assert report["selectedScenarios"] == ["recovery"]


def test_extended_stops_provider_failure_and_restores_environment(monkeypatch, tmp_path):
    mod = module()
    llm = OpenAICompatibleLLM(base_url="https://example.test", model="test")
    monkeypatch.setenv("WEAVEPATH_CONTEXT_BUDGET_CHARS", "55555")
    calls = []
    def fail(name, client, directory):
        calls.append(name)
        client.diagnostic = {"httpStatus": 404, "hint": "not found"}
        return {"id": name, "passed": False, "status": "failed"}
    monkeypatch.setattr(mod, "scenario", fail)
    output = tmp_path / "report.json"
    assert mod.extended(llm, output, repeat=3) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert len(report["results"]) == 21
    assert sum(r["status"] == "skipped" for r in report["results"]) == 20
    assert report["rulesPassed"] is False
    assert os.environ["WEAVEPATH_CONTEXT_BUDGET_CHARS"] == "55555"
