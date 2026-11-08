import altair as alt
import streamlit as st

from lib import data
from triage.config import fq

st.title("🧪 Harvey back-test")
st.caption("Same reports, same dispatch capacity: first-come-first-served vs the Triage queue. Notebooks 08 and 06.")

bt = data.q(f"""SELECT policy, capacity_per_hour, value FROM {fq('backtest_results')}
                WHERE metric = 'life_median_wait_min'
                  AND run_id = (SELECT max_by(run_id, created_at) FROM {fq('backtest_results')})""")
if bt.empty:
    st.info("Run notebook 08.")
else:
    bt["hours"] = bt.value / 60
    st.altair_chart(
        alt.Chart(bt).mark_bar().encode(
            x=alt.X("policy:N", title=None), y=alt.Y("hours:Q", title="Median wait, life-threatening (h)"),
            color=alt.Color("policy:N", scale=alt.Scale(range=["#94a3b8", "#dc2626"]), legend=None),
            column=alt.Column("capacity_per_hour:Q", title="Dispatch capacity (reports/hour)"),
        ),
    )

lt = data.q(f"""SELECT count(*) n, count_if(lead_time_h > 0) flagged_first, percentile(lead_time_h, 0.5) median_lead_h
                FROM {fq('facility_lead_times')}""")
if not lt.empty and lt.n[0]:
    c1, c2 = st.columns(2)
    c1.metric("Facilities flagged before their first rescue request", f"{int(lt.flagged_first[0])} / {int(lt.n[0])}")
    c2.metric("Median warning lead time", f"{lt.median_lead_h[0]:.1f} h")
