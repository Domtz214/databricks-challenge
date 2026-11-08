import altair as alt
import pandas as pd
import streamlit as st

from ui.backend import get_backend, incidents
from ui.components import CATEGORY_TONE, STATUS_TONE, chip, hero, kpi, short_job
from ui.timeutil import ago

be = get_backend()
hero("Lakehouse Doctor", "Your workspace's on-call reliability engineer: finds the incident behind the error.")

inc = incidents(be)
open_ = [i for i in inc if i["status"] in ("new", "diagnosed")]
verified = [i for i in inc if i["dx"] and i["dx"].get("fix_verified")]
resolved = [i for i in inc if i["status"] in ("fixed_and_rerun", "rerun")]
upstream = [i for i in inc if i["dx"] and i["dx"].get("fix_location") == "upstream_job"]

trend = pd.DataFrame(be.run_trend(7))
total = trend["runs"].sum() if not trend.empty else 0
ok = trend.loc[trend.outcome == "success", "runs"].sum() if not trend.empty else 0
health = f"{100 * ok / total:.0f}%" if total else "n/a"
try:
    fc = pd.DataFrame(be.failure_cost(30))
    spend = f"${fc['list_cost_usd'].fillna(0).sum():,.2f}" if not fc.empty else "$0.00"
except Exception:
    spend = "n/a"

c = st.columns(5)
with c[0]: kpi("Open incidents", len(open_), f"{sum(i['status'] == 'new' for i in inc)} not yet diagnosed", "red" if open_ else "green")
with c[1]: kpi("Job health · 7d", health, f"{ok} of {total} runs succeeded", "teal")
with c[2]: kpi("Verified fixes", len(verified), "patch proven against real source", "violet")
with c[3]: kpi("Root cause upstream", len(upstream), "a built-in fix would patch the symptom", "amber")
with c[4]: kpi("Spend on failed runs · 30d", spend, f"{len(resolved)} incidents resolved", None)

st.write("")
left, right = st.columns([1.35, 1])
with left:
    st.subheader("Run outcomes, last 7 days")
    if trend.empty:
        st.info("No completed runs in the last 7 days.")
    else:
        chart = alt.Chart(trend).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("day:N", title=None), y=alt.Y("runs:Q", title="runs", stack=True),
            color=alt.Color("outcome:N", scale=alt.Scale(domain=["success", "failed"], range=["#2DD4BF", "#F87171"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["day", "outcome", "runs"]).properties(height=280)
        st.altair_chart(chart, use_container_width=True)

with right:
    st.subheader("Needs attention")
    if not open_:
        st.success("Nothing open. Healthy lakehouse. Try the 🧪 Chaos lab.")
    for i in open_[:5]:
        with st.container(border=True):
            a, b = st.columns([4, 1])
            tags = chip(i["status"], STATUS_TONE.get(i["status"], "slate"))
            if i["dx"]:
                tags += chip(i["dx"].get("category", ""), CATEGORY_TONE.get(i["dx"].get("category"), "slate"))
                if i["dx"].get("fix_location") == "upstream_job":
                    tags += chip("upstream", "red")
            a.markdown(f"**{short_job(i['run_name'])}** · <span class='ld-muted'>{ago(i['start_time'])}</span><br>{tags}",
                       unsafe_allow_html=True)
            if b.button("Open", key=f"open_{i['run_id']}"):
                st.session_state.selected_run = i["run_id"]
                st.switch_page("views/incidents.py")

new = [i for i in inc if i["status"] == "new"]
if new and st.button(f"🩺 Diagnose all {len(new)} new incidents", type="primary"):
    for i in new:
        with st.status(f"Diagnosing {short_job(i['run_name'])}…", expanded=False) as s:
            dx, trace = be.diagnose(i["run_id"], on_event=lambda e: s.write(f"{e['tool']}"))
            be.save_diagnosis(i, dx, trace)
            s.update(label=f"{short_job(i['run_name'])}: {dx.get('category', 'done')}", state="complete")
    st.rerun()
