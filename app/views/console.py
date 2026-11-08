import json
import os

import pandas as pd
import pydeck as pdk
import streamlit as st

from lib import data
from triage.config import DEFAULT_CENTER

st.title("🛟 Triage · Dispatch console")
genie = os.getenv("TRIAGE_GENIE_URL")
if genie:
    st.caption(f"[Ask Genie a question about the data ↗]({genie})")


@st.fragment(run_every=10)  # live refresh without reloading the page
def live_board():
    q = data.priority_queue()
    if q.empty:
        st.info("No open incidents yet. Run notebook 03 (replay) and the pipeline.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open incidents", len(q))
    c2.metric("P1 (life-threatening)", int((q.tier == "P1").sum()))
    c3.metric("People reported", int(q.people_count.fillna(1).sum()))
    c4.metric("In active flood warning", int(q.in_flood_warning.fillna(False).sum()))

    q = q.assign(color=q.tier.map(data.TIER_COLORS))
    hexes = pdk.Layer("H3HexagonLayer", q, get_hexagon="h3_cell", get_fill_color="color",
                      pickable=True, extruded=False, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=1)
    fac = data.facilities()
    fac_layer = pdk.Layer("ScatterplotLayer", fac, get_position="[lon, lat]", get_radius=250,
                          get_fill_color=[37, 99, 235, 200], pickable=True)
    lat, lon = (q.lat.mean(), q.lon.mean()) if q.lat.notna().any() else DEFAULT_CENTER
    st.pydeck_chart(pdk.Deck(layers=[hexes, fac_layer], map_style=None,
                             initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=9.5),
                             tooltip={"text": "{tier} · {category}\n{n_reports} reports · {people_count} people"}))

    st.dataframe(q[["tier", "urgency", "category", "n_reports", "people_count", "vulnerable",
                    "in_flood_warning", "gauge_ft_above_flood", "forecast_risk", "first_reported_at", "incident_id"]],
                 hide_index=True, use_container_width=True)


live_board()

st.divider()
st.subheader("Incident detail + dispatcher agent")
q = data.priority_queue()
if not q.empty:
    labels = {r.incident_id: f"{r.tier} · {r.category} · {r.n_reports} reports" for r in q.itertuples()}
    inc_id = st.selectbox("Incident", list(labels), format_func=labels.get)
    inc = q[q.incident_id == inc_id].iloc[0]

    left, right = st.columns([1, 1])
    with left:
        st.markdown("**Reports**")
        for t in inc.sample_texts or []:
            st.markdown(f"> {t}")
        with st.expander("Why this priority? (urgency breakdown)"):
            st.json(json.loads(inc.breakdown_json))

    with right:
        if st.button("🤖 Ask dispatcher agent", type="primary"):
            with st.spinner("Checking flood context, shelters and guidance…"):
                st.session_state[f"rec_{inc_id}"] = data.agent().recommend(inc_id)
        rec = st.session_state.get(f"rec_{inc_id}")
        if rec:
            if "error" in rec:
                st.error(rec)
            else:
                st.markdown(f"### {rec.get('priority')} · {rec.get('resource', '').replace('_', ' ')}")
                if rec.get("destination"):
                    d = rec["destination"]
                    st.markdown(f"**Destination:** {d.get('name')} ({d.get('kind')}, {d.get('distance_km')} km)")
                for b in rec.get("reasoning", []):
                    st.markdown(f"- {b}")
                st.code(rec.get("dispatch_message", ""), language=None)
                st.caption("Sources: " + "; ".join(rec.get("citations", [])))
                a, r = st.columns(2)
                user = st.context.headers.get("X-Forwarded-Email", "coordinator")
                if a.button("✅ Approve dispatch"):
                    data.record_decision(inc_id, "approved", rec.get("resource"), rec, user)
                    st.success("Approved, logged to dispatch_decisions.")
                if r.button("✖️ Reject"):
                    data.record_decision(inc_id, "rejected", None, rec, user)
                    st.warning("Rejected.")
