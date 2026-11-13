"""Tests for Implementation pt2: the three testing scenarios.

Each scenario is run until idle and every check from nabd/scenarios.py must pass.
The scenarios are run:
  * with the rule-based Coordination (no API key, see conftest.py) on several seeds, and
  * with a fake LLM that makes a *different* valid choice (largest offer first), to show the
    checks test the agents' behaviour, not one particular allocation.
"""
import json
import re
from types import SimpleNamespace as NS

import pytest

from nabd import Simulation, SimulationConfig, llm_decider
from nabd.scenarios import SCENARIOS, load_scenario, run_scenario


def failed(result):
    return [f"{c.name}: {c.detail}" for c in result.checks if not c.passed]


@pytest.mark.parametrize("seed", [42, 1, 7])
@pytest.mark.parametrize("key", list(SCENARIOS))
def test_scenario_passes_rule_based(key, seed):
    result = run_scenario(key, SimulationConfig(seed=seed))
    assert result.passed, failed(result)
    assert not result.sim.coordination.requests or all(
        not r.active for r in result.sim.coordination.requests.values())


class LargestFirstLLM:
    """Fake LLM: reads the offers from the prompt and takes the largest offers first."""
    def __init__(self):
        self.calls = 0
        self.chat = NS(completions=NS(create=self.create))

    def create(self, messages, **kwargs):
        self.calls += 1
        data = json.loads(messages[-1]["content"])
        need, allocs = data["request"]["need"], []
        for o in sorted(data["offers"], key=lambda o: -o["quantity"]):
            if need <= 0:
                break
            q = min(o["quantity"], need)
            allocs.append({"supplier": o["supplier"], "quantity": q})
            need -= q
        answer = {"allocations": allocs, "reasoning": "Largest offer first (test LLM)."}
        return NS(choices=[NS(message=NS(content=json.dumps(answer)))])


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_scenario_passes_with_ai_decisions(key, monkeypatch):
    fake = LargestFirstLLM()
    monkeypatch.setattr(llm_decider, "get_client", lambda: fake)
    result = run_scenario(key, SimulationConfig(seed=42, use_ai_coordinator=True))
    assert fake.calls >= 1
    assert all(d["method"] == "AI" for d in result.sim.coordination.decision_log)
    # Scenario 1's "exact allocation" check expects 80 doses; with largest-first that is 80 from W.
    assert result.passed, failed(result)


def test_scenario1_matches_report_example():
    sim = run_scenario("normal").sim
    r = sim.coordination.requests["REQ-001"]
    assert {(a.supplier, a.quantity) for a in r.allocations} == {("B", 50), ("C", 30)}
    assert r.closed_step == 16


def test_scenario2_flagged_shipment_replaced():
    sim = run_scenario("cold_chain").sim
    r = sim.coordination.requests["REQ-001"]
    flagged = [a for a in r.allocations if a.status == "flagged"]
    assert len(flagged) == 1 and r.received == 80
    assert sum(a.quantity for a in r.allocations if a.status == "delivered") == 80


def test_scenario3_unresolved_then_reraised():
    sim = run_scenario("conflict").sim
    statuses = [(r.facility, r.status) for r in sim.coordination.requests.values()]
    assert ("D", "unresolved") in statuses and statuses[-1] == ("D", "fulfilled")
    assert sim.env.vehicles["V2"].available is False


def test_load_scenario_resets_and_ui_can_step():
    sim = Simulation(SimulationConfig(seed=42))
    sim.run(5)
    load_scenario(sim, "conflict")
    assert sim.step_count == 0
    assert sim.env.facilities["D"].eligible_stock("FLU") == 20
    sim.step()
    snap = sim.snapshot()
    assert snap["step"] == 1 and len(snap["agents"]) == 8


def test_schedule_when_fires_once():
    sim = Simulation(SimulationConfig(seed=42))
    fired = []
    sim.schedule_when(lambda: sim.step_count >= 3, lambda: fired.append(sim.step_count), "test trigger")
    sim.run(6)
    assert fired == [3]
    assert any(e.agent == "Scenario" and e.text == "test trigger" for e in sim.monitor.events)


def test_streamlit_app_runs_headless():
    """Smoke test of the interface: the page renders and the buttons advance the simulation."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("../app.py", default_timeout=60).run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "▶ Next step").click().run()
    assert not at.exception, at.exception
    assert at.session_state.sim.step_count == 1
    # Load scenario 3 and run until finished; all checks should pass.
    at.sidebar.selectbox[0].set_value("Scenario 3: Competing shortages + breakdown").run()
    next(b for b in at.button if b.label == "Load").click().run()
    next(b for b in at.button if b.label == "⏭ Run until finished").click().run()
    assert not at.exception, at.exception
    assert any(re.search(r"(\d+)/\1 checks passed", s.value) for s in at.success), [s.value for s in at.success]
    # every tab renders after the run
    assert len(at.tabs) == 6
