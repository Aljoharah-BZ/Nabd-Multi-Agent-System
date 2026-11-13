"""Testing scenarios for Nabd (Implementation pt2).

Three scripted scenarios exercise the four agent roles end to end. Each one:
  * sets up the network (interventions + scheduled / triggered events),
  * runs until the system is idle,
  * is checked against the expected behaviour from the design (Sections 3.3 - 3.6).

Scenario 1 - Normal emergency redistribution (baseline, report example)
Scenario 2 - Cold-chain temperature excursion during delivery
Scenario 3 - Competing shortages with a vehicle breakdown in transit

Used by the interface (app.py), the console runner (run_scenarios.py) and the
automated tests (tests/test_scenarios.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .messages import Performative
from .simulation import Simulation, SimulationConfig


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Scenario:
    key: str
    title: str
    objective: str
    # the five parts required for each scenario (project guideline 3.7)
    initial_conditions: list[str]
    agents_involved: list[str]
    events: list[str]
    expected: list[str]          # expected agent behaviours and interactions
    expected_outcome: str
    setup: Callable[[Simulation], None]
    checks: Callable[[Simulation], list[Check]]


@dataclass
class ScenarioResult:
    scenario: Scenario
    sim: Simulation
    steps: int
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


# ---- running to a stable end state -----------------------------------------------
def pending_realerts(sim: Simulation) -> list[str]:
    """Shortages that are waiting for the retry cooldown before being reported again."""
    out = []
    for fid, agent in sim.inventory_agents.items():
        f = sim.env.facilities[fid]
        for pid, until in agent.cooldown_until.items():
            if until > sim.step_count and f.threshold(pid) > 0 and f.eligible_stock(pid) < f.threshold(pid):
                out.append(f"{fid}/{pid} (re-alert at step {until})")
    return out


def is_settled(sim: Simulation) -> bool:
    """Idle (no messages, requests, deliveries, scheduled events) AND no shortage waiting to be re-raised.
    `Simulation.is_idle()` stops during a retry cooldown; scenarios run on until the re-raised
    request has also been handled."""
    return sim.is_idle() and not pending_realerts(sim)


def run_until_settled(sim: Simulation, max_steps: int = 200) -> int:
    start = sim.step_count
    sim.step()
    while not is_settled(sim) and sim.step_count - start < max_steps:
        sim.step()
    return sim.step_count - start


# ---- small helpers ------------------------------------------------------------
def _topics(sim: Simulation, topic: str):
    return [m for m in sim.bus.log if m.topic == topic]


def _first_msg_step(sim: Simulation, topic: str, conversation_id: Optional[str] = None) -> Optional[int]:
    for m in sim.bus.log:
        if m.topic == topic and (conversation_id is None or m.conversation_id == conversation_id):
            return m.sent_step
    return None


def _no_reservations_left(sim: Simulation) -> Check:
    left = {f.facility_id: dict(f.reserved_stock) for f in sim.env.facilities.values()
            if any(v for v in f.reserved_stock.values())}
    return Check("No reservations left behind", not left, f"left: {left}" if left else "all reservations released or used")


def _requests_by_facility(sim: Simulation) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in sim.coordination.requests.values():
        out.setdefault(r.facility, []).append(r)
    return out


# ---- Scenario 1: normal emergency redistribution --------------------------------
def _setup_normal(sim: Simulation) -> None:
    # Default network already holds the report example: Hospital A has 20/100 FLU doses.
    pass


def _checks_normal(sim: Simulation) -> list[Check]:
    reqs = list(sim.coordination.requests.values())
    r = reqs[0] if reqs else None
    a_stock = sim.env.facilities["A"]
    checks = [
        Check("Inventory-A detects shortage and opens one High-priority request",
              r is not None and len(reqs) == 1 and (r.facility, r.product, r.priority, r.initial_shortage) == ("A", "FLU", "High", 80),
              f"{len(reqs)} request(s)" + (f"; {r.request_id} {r.facility}/{r.product} {r.priority} {r.initial_shortage}" if r else "")),
    ]
    if r is None:
        return checks
    cfps = [m for m in sim.bus.log if m.performative == Performative.CFP and m.conversation_id == r.request_id]
    proposes = {sim.facility_of_agent(m.sender) for m in sim.bus.log
                if m.performative == Performative.PROPOSE and m.conversation_id == r.request_id}
    refuses = {sim.facility_of_agent(m.sender) for m in _topics(sim, "cfp_refused")}
    checks.append(Check("Coordination sends CFP to all 4 other facilities",
                        len({m.receiver for m in cfps}) == 4, f"CFP receivers: {sorted({m.receiver for m in cfps})}"))
    checks.append(Check("Suppliers B, C, W propose; D refuses (no surplus)",
                        proposes == {"B", "C", "W"} and refuses == {"D"},
                        f"PROPOSE from {sorted(proposes)}, REFUSE from {sorted(refuses)}"))
    accepted = sum(a.quantity for a in r.allocations)
    checks.append(Check("Allocation covers exactly the 80-dose shortage",
                        accepted == 80, ", ".join(f"{a.quantity} from {a.supplier}" for a in r.allocations)))
    reserved_step = _first_msg_step(sim, "reserved", r.request_id)
    deliver_step = _first_msg_step(sim, "deliver", r.request_id)
    checks.append(Check("Transport is requested only after a reservation is confirmed",
                        reserved_step is not None and deliver_step is not None and deliver_step > reserved_step,
                        f"first 'reserved' at step {reserved_step}, first delivery REQUEST at step {deliver_step}"))
    checks.append(Check("Request fulfilled: 80 doses received at Hospital A",
                        r.status == "fulfilled" and r.received == 80 and a_stock.eligible_stock("FLU") == 100,
                        f"status {r.status}, received {r.received}, A eligible {a_stock.eligible_stock('FLU')}/100"))
    perfs = {m.performative for m in sim.bus.log}
    checks.append(Check("All 7 ACL performatives used", perfs == set(Performative),
                        f"used: {sorted(p.value for p in perfs)}"))
    checks.append(_no_reservations_left(sim))
    return checks


# ---- Scenario 2: cold-chain excursion during delivery ---------------------------
def _setup_cold_chain(sim: Simulation) -> None:
    # The first shipment that is in transit reports 12 C (permitted range 2..8 C).
    sim.inject_shipment_excursion(reading=12.0)

    # Once the flagged shipment has been received at Hospital A, a pharmacist assesses it: DISCARD.
    def flagged_at_a() -> bool:
        return any(b.flagged for b in sim.env.facilities["A"].batches)

    def discard() -> None:
        for b in [b for b in sim.env.facilities["A"].batches if b.flagged]:
            sim.assess_batch(b.batch_id, "DISCARD")

    sim.schedule_when(flagged_at_a, discard, "Pharmacist assessment: DISCARD flagged batch at Hospital A")


def _checks_cold_chain(sim: Simulation) -> list[Check]:
    reqs = list(sim.coordination.requests.values())
    r = reqs[0] if reqs else None
    flagged_deliveries = [d for d in sim.env.deliveries.values() if d.status == "flagged"]
    alerts = _topics(sim, "shipment_flagged")
    checks = [
        Check("Cold-Chain detects the shipment excursion and alerts Transport + Coordination",
              sim.cold_chain.excursions_detected >= 1 and {m.receiver for m in alerts} >= {"Transport", "Coordination"},
              f"{sim.cold_chain.excursions_detected} excursion(s); alerts to {sorted({m.receiver for m in alerts})}"),
        Check("Flagged shipment arrives as 'flagged', not 'completed'",
              len(flagged_deliveries) == 1, ", ".join(f"{d.delivery_id} ({d.quantity})" for d in flagged_deliveries) or "none"),
    ]
    if r is None:
        return checks + [Check("Request opened", False, "no request")]
    flagged_alloc = [a for a in r.allocations if a.status == "flagged"]
    checks.append(Check("Flagged quantity is not counted as received",
                        bool(flagged_alloc) and r.received == sum(a.quantity for a in r.allocations if a.status == "delivered"),
                        f"flagged allocation(s): {[a.allocation_id for a in flagged_alloc]}; received {r.received}"))
    checks.append(Check("Coordination recovers with a new CFP round (replacement supplier)",
                        r.round >= 2, f"{r.round} CFP round(s)"))
    checks.append(Check("Request still fulfilled with eligible stock",
                        r.status == "fulfilled" and r.received == 80 and sim.env.facilities["A"].eligible_stock("FLU") >= 100,
                        f"status {r.status}, received {r.received}/80, A eligible {sim.env.facilities['A'].eligible_stock('FLU')}"))
    discarded = [m for m in _topics(sim, "assessment")
                 if m.sender == "ColdChain" and m.content.get("decision") == "DISCARD"]
    checks.append(Check("DISCARD assessment removes the flagged batch",
                        bool(discarded) and sim.env.facilities["A"].flagged_stock("FLU") == 0,
                        f"{len(discarded)} batch(es) discarded by Cold-Chain; A flagged stock {sim.env.facilities['A'].flagged_stock('FLU')}"))
    checks.append(_no_reservations_left(sim))
    return checks


# ---- Scenario 3: competing shortages + vehicle breakdown ------------------------
def _setup_conflict(sim: Simulation) -> None:
    # Hospital D uses 60 doses at the same time -> D: 20/80 -> a second High-priority FLU shortage.
    sim.consume_stock("D", "FLU", 60)
    sim.scenario_baseline = {fid: f.transferable_stock("FLU") for fid, f in sim.env.facilities.items()}

    # The first delivery that is in transit breaks down (cold chain broken, cargo returned flagged).
    def in_transit():
        return [d for d in sim.env.deliveries.values() if d.status == "in_progress"]

    def breakdown() -> None:
        d = in_transit()[0]
        sim.break_down_vehicle(d.delivery_id)

    sim.schedule_when(lambda: bool(in_transit()), breakdown, "Vehicle breakdown on first delivery in transit")


def _checks_conflict(sim: Simulation) -> list[Check]:
    reqs = list(sim.coordination.requests.values())
    first = {}
    for r in reqs:
        first.setdefault(r.facility, r)
    failed = [d for d in sim.env.deliveries.values() if d.status == "failed" and "breakdown" in d.failure_reason]
    broken = [v for v in sim.env.vehicles.values() if not v.available]
    returned = [b for f in sim.env.facilities.values() for b in f.batches if b.batch_id.endswith("-R") and b.flagged]

    # One open request per facility/product: intervals for the same key must not overlap.
    overlap = []
    by_key: dict[tuple, list] = {}
    for r in reqs:
        by_key.setdefault((r.facility, r.product), []).append(r)
    for key, rs in by_key.items():
        for a, b in zip(rs, rs[1:]):
            if a.closed_step is None or b.opened_step < a.closed_step:
                overlap.append(f"{a.request_id}/{b.request_id}")

    # Unresolved requests must be recorded and the shortage raised again after the cooldown.
    unresolved = [r for r in reqs if r.status == "unresolved"]
    re_raised = all(any(o.facility == u.facility and o.product == u.product and o.opened_step > u.closed_step
                        for o in reqs) for u in unresolved)

    # Double-allocation check: what each supplier loaded onto vehicles vs. its surplus at the start.
    base = getattr(sim, "scenario_baseline", {})
    shipped: dict[str, int] = {}
    for d in sim.env.deliveries.values():
        if d.cargo or d.status in ("in_progress", "completed", "flagged") or "breakdown" in d.failure_reason:
            shipped[d.supplier] = shipped.get(d.supplier, 0) + d.quantity
    over = [s for s, q in shipped.items() if q > base.get(s, 0)]
    shipped_txt = ", ".join(f"{s} {q}/{base.get(s, 0)}" for s, q in sorted(shipped.items()))

    a_fac, d_fac = sim.env.facilities["A"], sim.env.facilities["D"]
    checks = [
        Check("A (High) and D (Medium) shortages open at the same step",
              {"A", "D"} <= set(first) and first["A"].opened_step == first["D"].opened_step
              and (first["A"].priority, first["D"].priority) == ("High", "Medium"),
              ", ".join(f"{r.request_id} {r.facility}/{r.product} {r.priority} @step {r.opened_step}" for r in reqs)),
        Check("Never more than one open request per facility/product",
              not overlap, "overlap: " + ", ".join(overlap) if overlap else f"{len(reqs)} request(s), no overlap"),
        Check("Competing requests never take more than a supplier's surplus (no double allocation)",
              not over, f"shipped/surplus: {shipped_txt}; {len(_topics(sim, 'reservation_failed'))} "
                        f"reservation refusal(s) on recheck" + (f"; OVER: {over}" if over else "")),
        Check("Breakdown fails the delivery and takes the vehicle out of service",
              bool(failed) and bool(broken),
              f"failed: {[d.delivery_id for d in failed]}; unavailable: {[v.vehicle_id for v in broken]}"),
        Check("Returned cargo is stored as flagged (needs assessment)",
              bool(returned), f"{[(b.batch_id, b.quantity) for b in returned]}"),
        Check("Transport refusals release the canceled reservations",
              len(_topics(sim, "release_reservation")) == len(_topics(sim, "no_vehicle")),
              f"{len(_topics(sim, 'no_vehicle'))} vehicle refusal(s), "
              f"{len(_topics(sim, 'release_reservation'))} reservation release(s)"),
        Check("Coordination retries alternatives; unresolved requests are re-raised after cooldown",
              any(r.round >= 2 for r in reqs) and re_raised,
              "; ".join(f"{r.request_id}: {r.round} round(s), {r.status}" for r in reqs)),
        Check("Every request is closed with a recorded outcome",
              bool(reqs) and all(not r.active for r in reqs),
              ", ".join(f"{r.request_id} {r.status} ({r.received}/{r.total_needed})" for r in reqs)),
        Check("Both hospitals end at or above their FLU minimum",
              a_fac.eligible_stock("FLU") >= a_fac.threshold("FLU") and d_fac.eligible_stock("FLU") >= d_fac.threshold("FLU"),
              f"A {a_fac.eligible_stock('FLU')}/{a_fac.threshold('FLU')}, D {d_fac.eligible_stock('FLU')}/{d_fac.threshold('FLU')}"),
        _no_reservations_left(sim),
    ]
    return checks


SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario(
        key="normal",
        title="Scenario 1 - Normal emergency redistribution",
        objective="Show the full shortage -> negotiation -> reservation -> delivery workflow with no failures.",
        initial_conditions=[
            "Hospital A has 20 influenza vaccine doses; its minimum is 100 (shortage of 80, High priority).",
            "Hospital B has 150 (min 100), Hospital C 140 (min 100), Hospital D 80 (min 80), Warehouse W 400 (min 200).",
            "All facilities are online and all 3 refrigerated vehicles are available.",
        ],
        agents_involved=["Inventory agents A, B, C, D, W", "Emergency Coordination", "Transport and Delivery",
                         "Cold-Chain Monitoring (monitors only, no alerts)"],
        events=["Step 1: Inventory-A detects the shortage. No failures are injected."],
        expected=["Inventory-A sends INFORM (80-dose High shortage); Coordination opens REQ-001.",
                  "Coordination sends CFP to B, C, D and W.",
                  "B (50, 20 min), C (40, 35 min) and W (80, 60 min) send PROPOSE; D sends REFUSE.",
                  "Coordination sends ACCEPT for 50 from B and 30 from C, and REJECT-PROPOSAL to W.",
                  "B and C reserve the stock and confirm with INFORM.",
                  "Only then Coordination sends REQUEST to Transport; Transport assigns vehicles.",
                  "Cold-Chain checks the shipments every step; all readings are normal."],
        expected_outcome="REQ-001 is fulfilled (80 of 80 received) and Hospital A ends with 100 doses.",
        setup=_setup_normal, checks=_checks_normal,
    ),
    "cold_chain": Scenario(
        key="cold_chain",
        title="Scenario 2 - Cold-chain excursion during delivery",
        objective="Check that a temperature-flagged shipment is excluded and the shortage is still covered.",
        initial_conditions=["Same network and shortage as Scenario 1 (Hospital A: 20 of 100 doses)."],
        agents_involved=["Cold-Chain Monitoring", "Transport and Delivery", "Emergency Coordination",
                         "Inventory agents A, B, C, D, W"],
        events=["Failure: the first shipment on the road reads 12 C (allowed range 2-8 C).",
                "When the flagged batch reaches Hospital A, a pharmacist assessment DISCARDs it."],
        expected=["Cold-Chain flags the shipment and sends INFORM alerts to Transport and Coordination.",
                  "The shipment arrives as 'flagged'; its quantity is not counted as received.",
                  "Coordination starts a new CFP round and accepts a replacement supplier.",
                  "Cold-Chain applies the DISCARD and Inventory-A removes the flagged batch."],
        expected_outcome="REQ-001 is still fulfilled with usable stock and the flagged batch is discarded.",
        setup=_setup_cold_chain, checks=_checks_cold_chain,
    ),
    "conflict": Scenario(
        key="conflict",
        title="Scenario 3 - Competing shortages + vehicle breakdown",
        objective="Check conflict resolution and recovery when two hospitals compete and a vehicle fails.",
        initial_conditions=["Same network as Scenario 1.",
                            "Hospital D uses 60 doses at step 0, so A (20/100, High) and D (20/80, Medium) "
                            "are short at the same time."],
        agents_involved=["Inventory agents A and D (two requesters) and B, C, W (suppliers)",
                         "Emergency Coordination", "Transport and Delivery", "Cold-Chain Monitoring"],
        events=["Conflict: two requests compete for the same supplier stock.",
                "Failure: the first vehicle on the road breaks down and stays out of service."],
        expected=["Two requests open at the same step; both CFPs reach the same suppliers.",
                  "Suppliers recheck stock on ACCEPT, so no supplier gives more than its surplus "
                  "(B and C REFUSE D's reservations).",
                  "The breakdown fails the delivery; the cargo returns to the supplier as flagged.",
                  "With one chilled vehicle left, Transport REFUSEs some deliveries; reservations are released.",
                  "A request may close UNRESOLVED; after the retry cooldown the hospital reports it again."],
        expected_outcome="Every request ends with a recorded outcome and both hospitals end at or above "
                         "their minimum.",
        setup=_setup_conflict, checks=_checks_conflict,
    ),
}


def load_scenario(sim: Simulation, key: str) -> Scenario:
    """Reset `sim` and apply the scenario setup (used by the interface)."""
    sc = SCENARIOS[key]
    sim.reset()
    sim.monitor.log_event(0, "Scenario", f"Loaded: {sc.title}", "warning")
    sc.setup(sim)
    return sc


def run_scenario(key: str, config: Optional[SimulationConfig] = None, max_steps: int = 200) -> ScenarioResult:
    sim = Simulation(config or SimulationConfig(seed=42))
    sc = load_scenario(sim, key)
    steps = run_until_settled(sim, max_steps=max_steps)
    return ScenarioResult(sc, sim, steps, sc.checks(sim))
