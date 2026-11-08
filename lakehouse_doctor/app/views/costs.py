import altair as alt
import pandas as pd
import streamlit as st

from ui.backend import get_backend
from ui.components import hero, kpi

be = get_backend()
hero("Costs", "What's burning DBUs, and how much went to runs that failed. From system.billing (lags a few hours).")

days = st.segmented_control("Window", [7, 14, 30], default=7, format_func=lambda d: f"{d} days") or 7
try:
    drivers = pd.DataFrame(be.cost_drivers(days))
    failed = pd.DataFrame(be.failure_cost(30))
    slow = pd.DataFrame(be.slow_queries())
except Exception as e:
    st.warning(f"System tables not readable by the app's identity: {e}")
    st.stop()

c = st.columns(3)
with c[0]: kpi(f"DBUs · {days}d", f"{drivers['dbus'].sum():,.1f}" if not drivers.empty else "0")
with c[1]: kpi(f"List cost · {days}d", f"${drivers['list_cost_usd'].fillna(0).sum():,.2f}" if not drivers.empty else "$0")
with c[2]: kpi("Spent on failed runs · 30d", f"${failed['list_cost_usd'].fillna(0).sum():,.2f}" if not failed.empty else "$0",
               "money a faster fix saves", "red")

st.write("")
left, right = st.columns([1.3, 1])
with left:
    st.subheader("Top cost drivers")
    if drivers.empty:
        st.caption("No usage recorded yet (billing data lags).")
    else:
        drivers["resource"] = drivers["product"].astype(str) + " · " + drivers["resource_id"].astype(str).str[-28:]
        st.altair_chart(alt.Chart(drivers).mark_bar(cornerRadiusEnd=3, color="#2DD4BF").encode(
            x=alt.X("dbus:Q", title="DBUs"), y=alt.Y("resource:N", sort="-x", title=None),
            tooltip=["product", "resource_id", "dbus", "list_cost_usd"]).properties(height=320), use_container_width=True)
with right:
    st.subheader("Failed-run spend by job")
    if failed.empty:
        st.caption("None recorded.")
    else:
        st.dataframe(failed, hide_index=True, use_container_width=True)

st.subheader("Slowest queries · 24h")
if slow.empty:
    st.caption("No finished queries in the last day.")
else:
    slow["seconds"] = (slow["total_duration_ms"] / 1000).round(1)
    slow["GB read"] = (slow["read_bytes"].fillna(0) / 1e9).round(2)
    st.dataframe(slow[["seconds", "GB read", "produced_rows", "executed_by", "statement_text"]], hide_index=True,
                 use_container_width=True)
