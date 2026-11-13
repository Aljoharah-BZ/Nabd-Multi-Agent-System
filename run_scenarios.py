"""Run the three Nabd testing scenarios from the console (Implementation pt2).

    python run_scenarios.py                    # all three scenarios, summary + checks
    python run_scenarios.py conflict --log     # one scenario, with the full event log
    python run_scenarios.py --no-ai            # Coordination rule-based only
    python run_scenarios.py --export out       # out/<scenario>/messages.csv, state.json, results.md

Scenario keys: normal, cold_chain, conflict
"""
from __future__ import annotations

import argparse
import os
import sys

from nabd import SimulationConfig
from nabd.scenarios import SCENARIOS, run_scenario


def results_markdown(res) -> str:
    sc, sim = res.scenario, res.sim
    m = sim.monitor.metrics()
    lines = [f"## {sc.title}", "", f"**Objective.** {sc.objective}", "", "**Initial conditions**", ""]
    lines += [f"- {x}" for x in sc.initial_conditions]
    lines += ["", "**Agents involved**", ""] + [f"- {x}" for x in sc.agents_involved]
    lines += ["", "**Events**", ""] + [f"- {x}" for x in sc.events]
    lines += ["", "**Expected behaviours and interactions**", ""]
    lines += [f"{i}. {x}" for i, x in enumerate(sc.expected, 1)]
    lines += ["", f"**Expected outcome.** {sc.expected_outcome}"]
    lines += ["", "**Results**", "", "| Check | Result | Observed |", "|---|---|---|"]
    lines += [f"| {c.name} | {'PASS' if c.passed else 'FAIL'} | {c.detail} |" for c in res.checks]
    lines += ["", f"Steps: {sim.step_count} · messages: {m['messages_total']} {m['messages_by_performative']}", ""]
    lines += ["| Request | Facility | Product | Priority | Received | Status | Rounds | Opened → closed |",
              "|---|---|---|---|---|---|---|---|"]
    for r in sim.coordination.requests_table():
        lines.append(f"| {r['request_id']} | {r['facility']} | {r['product']} | {r['priority']} | "
                     f"{r['received']}/{r['total_needed']} | {r['status']} | {r['round']} | "
                     f"{r['opened_step']} → {r['closed_step']} |")
    lines += ["", "**Allocation decisions**", ""]
    for d in sim.coordination.decision_log:
        chosen = ", ".join(f"{a['quantity']} from {a['supplier']}" for a in d["allocations"])
        lines.append(f"- step {d['step']} {d['request_id']} round {d['round']} [{d['method']}]: {chosen}. {d['reasoning']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*", choices=[[], *SCENARIOS], default=[],
                    help="scenario keys (default: all)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-ai", action="store_true", help="Coordination uses the rule-based choice only")
    ap.add_argument("--log", action="store_true", help="print the full event log")
    ap.add_argument("--export", metavar="DIR", help="write messages.csv, state.json and results.md per scenario")
    args = ap.parse_args()

    keys = args.scenarios or list(SCENARIOS)
    all_ok = True
    for key in keys:
        res = run_scenario(key, SimulationConfig(seed=args.seed, use_ai_coordinator=not args.no_ai))
        sim = res.sim
        print("=" * 100)
        print(res.scenario.title)
        print("=" * 100)
        if args.log:
            for e in sim.monitor.events:
                print(e)
            print()
        for c in res.checks:
            print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}\n         {c.detail}")
        m = sim.monitor.metrics()
        print(f"\n  {'ALL CHECKS PASSED' if res.passed else 'SOME CHECKS FAILED'} - "
              f"{sim.step_count} steps, {m['messages_total']} messages, "
              f"{m['requests_fulfilled']} fulfilled / {m['requests_unresolved']} unresolved request(s)\n")
        all_ok &= res.passed
        if args.export:
            out = os.path.join(args.export, key)
            os.makedirs(out, exist_ok=True)
            sim.monitor.export_messages_csv(os.path.join(out, "messages.csv"))
            sim.monitor.export_json(os.path.join(out, "state.json"))
            with open(os.path.join(out, "results.md"), "w", encoding="utf-8") as fh:
                fh.write(results_markdown(res))
            print(f"  exported to {out}/")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
