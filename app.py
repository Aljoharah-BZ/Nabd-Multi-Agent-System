# Nabd - Python interface (Implementation part 2)
# Run with:  streamlit run app.py

import os
import time

import pandas as pd
import streamlit as st

from nabd import Simulation, SimulationConfig
from nabd.scenarios import SCENARIOS, is_settled, load_scenario, run_until_settled

st.set_page_config(page_title="Nabd Simulation", page_icon="🏥", layout="wide")

# colours used in the tables
GREEN = "background-color: #d1fae5; color: #065f46"
RED = "background-color: #fee2e2; color: #991b1b"
YELLOW = "background-color: #fef3c7; color: #92400e"
BLUE = "background-color: #dbeafe; color: #1e40af"
GREY = "color: #6b7280"

COLOURS = {
    "Pass": GREEN, "Fail": RED, "Waiting": YELLOW,
    "Idle": GREY, "Working": BLUE, "Waiting for reply": YELLOW, "Alert": RED,
    "Fulfilled": GREEN, "Partial": YELLOW, "In progress": BLUE, "Unresolved": RED,
    "Delivered": GREEN, "On the road": BLUE, "Vehicle assigned": BLUE, "Flagged": RED, "Failed": RED,
    "Reserved": BLUE, "Accepted": BLUE, "Cancelled": GREY,
    "Available": GREEN, "Busy": BLUE, "Out of service": RED,
    "Yes": RED, "No": GREEN,
}

# simple words instead of the internal status names
STATE_NAMES = {"idle": "Idle", "active": "Working", "waiting": "Waiting for reply", "alert": "Alert"}
REQUEST_NAMES = {"open": "In progress", "fulfilled": "Fulfilled", "partial": "Partial", "unresolved": "Unresolved"}
DELIVERY_NAMES = {"assigned": "Vehicle assigned", "in_progress": "On the road", "completed": "Delivered",
                  "failed": "Failed", "flagged": "Flagged", "delivered": "Delivered", "reserved": "Reserved",
                  "accepted": "Accepted", "canceled": "Cancelled", "cancelled": "Cancelled"}
VEHICLE_NAMES = {"available": "Available", "busy": "Busy", "unavailable": "Out of service"}


def colour(value):
    return COLOURS.get(value, "")


def show_table(rows, colour_columns):
    df = pd.DataFrame(rows)
    styled = df.style.map(colour, subset=colour_columns)
    st.dataframe(styled, hide_index=True, width="stretch")


def product_name(product_id):
    return sim.env.products[product_id].name


# scenario names shown in the dropdown
SCENARIO_NAMES = {
    "Free run (no scenario)": None,
    "Scenario 1: Normal redistribution": "normal",
    "Scenario 2: Cold-chain excursion": "cold_chain",
    "Scenario 3: Competing shortages + breakdown": "conflict",
}

# keep the simulation between button clicks
if "sim" not in st.session_state:
    use_ai = bool(os.getenv("LLM_API_KEY"))
    st.session_state.sim = Simulation(SimulationConfig(seed=42, use_ai_coordinator=use_ai))
    st.session_state.scenario = None

sim = st.session_state.sim


# ---------------- Sidebar ----------------
st.sidebar.title("🏥 Nabd")
st.sidebar.header("Scenario", divider="blue")
chosen = st.sidebar.selectbox("Choose a scenario", list(SCENARIO_NAMES.keys()))
if st.sidebar.button("Load", type="primary", width="stretch"):
    st.session_state.scenario = SCENARIO_NAMES[chosen]
    if st.session_state.scenario is None:
        sim.reset()
    else:
        load_scenario(sim, st.session_state.scenario)

st.sidebar.header("Controls", divider="green")
if st.sidebar.button("▶ Next step", width="stretch"):
    sim.step()
if st.sidebar.button("⏩ Run 10 steps", width="stretch"):
    sim.run(10)
if st.sidebar.button("⏭ Run until finished", type="primary", width="stretch"):
    run_until_settled(sim)
if st.sidebar.button("🔄 Reset", width="stretch"):
    if st.session_state.scenario is None:
        sim.reset()
    else:
        load_scenario(sim, st.session_state.scenario)
auto_run = st.sidebar.toggle("Auto run (1 step every second)")
if auto_run and not (is_settled(sim) and sim.step_count > 0):
    sim.step()

st.sidebar.header("Settings", divider="orange")
has_key = bool(os.getenv("LLM_API_KEY"))
sim.config.use_ai_coordinator = st.sidebar.checkbox(
    "Use AI (LLM) for allocation", value=sim.config.use_ai_coordinator, disabled=not has_key)
if not has_key:
    st.sidebar.caption("No API key found, the rule-based method is used.")

with st.sidebar.expander("Parameters"):
    sim.config.cfp_response_deadline = st.slider(
        "CFP deadline (steps)", 1, 10, sim.config.cfp_response_deadline,
        help="How many steps Coordination waits for offers before deciding (default 3).")
    sim.config.max_cfp_rounds = st.slider(
        "Max CFP rounds", 1, 6, sim.config.max_cfp_rounds,
        help="How many times Coordination retries before a request is unresolved.")
    sim.config.retry_cooldown = st.slider(
        "Retry cooldown (steps)", 1, 30, sim.config.retry_cooldown,
        help="How long a hospital waits before reporting an unresolved shortage again.")
    sim.config.step_minutes = st.slider(
        "Minutes per step", 5, 30, sim.config.step_minutes, 5)

with st.sidebar.expander("Random events"):
    st.caption("Chance per step. 0 = off (the scenarios use 0).")
    sim.config.p_demand_spike = st.slider("Demand spike", 0.0, 0.3, sim.config.p_demand_spike, 0.01)
    sim.config.p_temp_excursion = st.slider("Temperature problem", 0.0, 0.3, sim.config.p_temp_excursion, 0.01)
    sim.config.p_vehicle_breakdown = st.slider("Vehicle breakdown", 0.0, 0.2, sim.config.p_vehicle_breakdown, 0.01)

with st.sidebar.expander("Change stock"):
    fac_id = st.selectbox("Facility", list(sim.env.facilities.keys()), format_func=sim.facility_name)
    prod_id = st.selectbox("Product ", list(sim.env.products.keys()),
                           format_func=lambda p: sim.env.products[p].name)
    amount = st.slider("Units used", 10, 200, 60, 10)
    if st.button("Use stock", width="stretch"):
        sim.consume_stock(fac_id, prod_id, amount)
    facility = sim.env.facilities[fac_id]
    new_min = st.slider("Minimum threshold", 0, 500, facility.threshold(prod_id), 10)
    if st.button("Set minimum", width="stretch"):
        sim.set_threshold(fac_id, prod_id, new_min)


# ---------------- Main page ----------------
st.title("🏥 Nabd - Emergency Medical Supply Simulation")
st.markdown("Multi-agent system that moves :blue[medical supplies] between hospitals during "
            ":red[shortages] (synthetic data).")

metrics = sim.monitor.metrics()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Step", sim.step_count, border=True)
c2.metric("Open requests", metrics["requests_open"], border=True)
c3.metric("Fulfilled", metrics["requests_fulfilled"], border=True)
c4.metric("Active deliveries", metrics["deliveries_active"], border=True)

finished = is_settled(sim) and sim.step_count > 0

# scenario results
if st.session_state.scenario is not None:
    scenario = SCENARIOS[st.session_state.scenario]
    st.subheader("🧪 " + scenario.title, divider="violet")
    st.write(scenario.objective)
    with st.expander("Scenario description"):
        st.write("**Initial conditions**")
        for line in scenario.initial_conditions:
            st.write("- " + line)
        st.write("**Agents involved**")
        for line in scenario.agents_involved:
            st.write("- " + line)
        st.write("**Events**")
        for line in scenario.events:
            st.write("- " + line)
        st.write("**Expected agent behaviours and interactions**")
        for i, line in enumerate(scenario.expected, 1):
            st.write(f"{i}. {line}")
        st.write("**Expected outcome**")
        st.write(scenario.expected_outcome)

    rows = []
    details = []
    for check in scenario.checks(sim):
        if check.passed:
            result = "Pass"
        elif finished:
            result = "Fail"
        else:
            result = "Waiting"
        rows.append({"Check": check.name, "Result": result})
        details.append(f"**{check.name}:** {check.detail}")
    show_table(rows, ["Result"])
    with st.expander("Show details of each check"):
        for line in details:
            st.write(line)

    if finished:
        passed = sum(1 for r in rows if r["Result"] == "Pass")
        if passed == len(rows):
            st.success(f"✅ {passed}/{len(rows)} checks passed in {sim.step_count} steps.")
        else:
            st.error(f"❌ {passed}/{len(rows)} checks passed in {sim.step_count} steps.")
    else:
        st.info("⏳ Click 'Run until finished' to get the final result.")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["🤖 Agents", "📦 Inventory", "📋 Requests", "🚚 Transport", "🌡️ Cold Chain", "✉️ Messages"])

# Agents tab: what each agent is doing now
with tab1:
    rows = []
    for agent in sim.agents:
        a = agent.to_dict()
        rows.append({
            "Agent": a["agent_id"],
            "Role": a["agent_type"],
            "State": STATE_NAMES.get(a["state"], a["state"]),
            "Doing now": a["status"],
        })
    show_table(rows, ["State"])

# Inventory tab: stock compared to the minimum
with tab2:
    product = st.selectbox("Product", list(sim.env.products.keys()), format_func=product_name)
    rows = []
    for f in sim.env.facilities.values():
        rows.append({
            "Facility": f.name,
            "Stock": f.eligible_stock(product),
            "Minimum": f.threshold(product),
            "Shortage": "Yes" if f.eligible_stock(product) < f.threshold(product) else "No",
        })
    df = pd.DataFrame(rows)
    st.bar_chart(df.set_index("Facility")[["Stock", "Minimum"]], stack=False, color=["#0d9488", "#f59e0b"])
    show_table(rows, ["Shortage"])

    with st.expander("Show all stock values"):
        st.caption("Eligible = Total - Flagged.  Transferable = Eligible - Minimum - Reserved (never below 0).")
        rows = []
        for f in sim.env.facilities.values():
            rows.append({
                "Facility": f.name,
                "Total": f.total_stock(product),
                "Flagged": f.flagged_stock(product),
                "Eligible": f.eligible_stock(product),
                "Minimum": f.threshold(product),
                "Reserved": f.reserved_stock.get(product, 0),
                "Transferable": f.transferable_stock(product),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# Requests tab: the emergency requests handled by the Coordination agent
with tab3:
    requests = list(sim.coordination.requests.values())
    if len(requests) == 0:
        st.write("No requests yet. A request opens when a hospital goes below its minimum.")
    else:
        rows = []
        for r in requests:
            rows.append({
                "Request": r.request_id,
                "Hospital": sim.facility_name(r.facility),
                "Product": product_name(r.product),
                "Priority": r.priority,
                "Received": f"{r.received} of {r.total_needed}",
                "Still needed": r.remaining_shortage,
                "Status": REQUEST_NAMES.get(r.status, r.status),
            })
        show_table(rows, ["Status"])

        req_id = st.selectbox("Show suppliers for request", [r.request_id for r in requests])
        req = sim.coordination.requests[req_id]
        alloc_rows = []
        for a in req.allocations:
            alloc_rows.append({"Supplier": sim.facility_name(a.supplier), "Quantity": a.quantity,
                               "Status": DELIVERY_NAMES.get(a.status, a.status)})
        if alloc_rows:
            show_table(alloc_rows, ["Status"])
        else:
            st.write("Waiting for offers from the other facilities.")

        with st.expander("Request history"):
            for step, text in req.history:
                st.write(f"Step {step}: {text}")

# Transport tab: deliveries and vehicles
with tab4:
    rows = []
    for d in sim.env.deliveries.values():
        status = DELIVERY_NAMES.get(d.status, d.status)
        if d.flagged:
            status = "Flagged"
        rows.append({"From": sim.facility_name(d.supplier), "To": sim.facility_name(d.receiver),
                     "Quantity": f"{d.quantity} {product_name(d.product_id)}",
                     "Vehicle": sim.env.vehicles[d.vehicle_id].name,
                     "ETA (min)": d.eta_minutes,
                     "Status": status})
    st.write("**Deliveries**")
    if rows:
        show_table(rows, ["Status"])
    else:
        st.write("No deliveries yet.")

    rows = []
    for v in sim.env.vehicles.values():
        location = sim.facility_name(v.location) if v.location else "On the road"
        rows.append({"Vehicle": v.name, "Location": location, "Capacity": v.capacity,
                     "Temperature": f"{v.temp_min:g} to {v.temp_max:g} °C",
                     "Status": VEHICLE_NAMES.get(v.status, v.status)})
    st.write("**Vehicles**")
    show_table(rows, ["Status"])

# Cold chain tab: temperature problems only
with tab5:
    flagged = sim.flagged_batches()
    if len(flagged) == 0:
        st.success("No flagged batches. All stock is usable.")
    else:
        st.write("These batches went out of their temperature range. Decide what to do with them:")
    for b in flagged:
        col1, col2, col3 = st.columns([3, 1, 1])
        col1.error(f"🚨 {b['quantity']} {product_name(b['product'])} at {sim.facility_name(b['facility'])}")
        if col2.button("Release", key="release_" + b["batch_id"]):
            sim.assess_batch(b["batch_id"], "RELEASE")
            st.write("Release sent, it will apply on the next step.")
        if col3.button("Discard", key="discard_" + b["batch_id"]):
            sim.assess_batch(b["batch_id"], "DISCARD")
            st.write("Discard sent, it will apply on the next step.")

    readings = sim.snapshot()["temperatures"]
    bad = [t for t in readings if not t["in_range"]]
    if readings and not bad:
        st.write(f"🌡️ All {len(readings)} temperature readings in the last step were normal.")
    for t in bad:
        st.warning(f"{t['target']}: {t['reading']} °C (allowed {t['min']} to {t['max']} °C)")

    if readings:
        with st.expander("Show all temperature readings"):
            rows = []
            for t in readings:
                rows.append({"Batch / shipment": t["target"], "Product": product_name(t["product"]),
                             "Reading (°C)": t["reading"], "Allowed": f"{t['min']:g} to {t['max']:g}",
                             "OK?": "No" if not t["in_range"] else "Yes"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# Messages tab: how the agents talk to each other
with tab6:
    rows = []
    for m in sim.bus.log:
        rows.append({"Step": m.sent_step, "From": m.sender, "To": m.receiver,
                     "Type": m.performative.value, "Message": m.text,
                     "Ontology": m.ontology.value, "Request": m.conversation_id})
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.iloc[::-1], hide_index=True, width="stretch")
        st.download_button("Download message log (CSV)", df.to_csv(index=False), "messages.csv")
    else:
        st.write("No messages yet.")

# auto run: wait one second, then the page reloads and does the next step
if auto_run and not (is_settled(sim) and sim.step_count > 0):
    time.sleep(1)
    st.rerun()
