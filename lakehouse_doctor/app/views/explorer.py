import pandas as pd
import streamlit as st

from ui.backend import get_backend
from ui.components import chip, hero, lineage_dot

be = get_backend()
hero("Lineage explorer", "Parsed from every job's code + Delta table history. Works even when system lineage is empty.")

edges = be.lineage_edges()
tables = sorted({t for e in edges for t in (e.get("source"), e.get("target")) if t})
if not tables:
    st.info("No lineage yet: create some jobs (Chaos lab) and refresh.")
    st.stop()

default = tables.index("workspace.doctor.orders_clean") if "workspace.doctor.orders_clean" in tables else 0
table = st.selectbox("Table", tables, index=default)

from doctor.lineage import neighborhood  # noqa: E402

n = neighborhood(edges, table)
blast = sorted({b["writes"] for b in n["downstream_blast_radius"] if b["writes"]})
c = st.columns(3)
c[0].metric("Written by", len(n["written_by_jobs"]))
c[1].metric("Upstream tables", len(n["upstream_tables"]))
c[2].metric("Downstream blast radius", len(n["downstream_blast_radius"]))
st.graphviz_chart(lineage_dot(edges, focus=table, blast=blast), use_container_width=True)

st.subheader("Version history")
try:
    hist = pd.DataFrame(be.table_history(table))
except Exception as e:
    st.warning(f"Could not read history: {e}")
    st.stop()
if hist.empty:
    st.caption("No history.")
    st.stop()
st.dataframe(hist[["version", "timestamp", "operation", "job_name", "job_run_id", "user"]], hide_index=True,
             use_container_width=True)

versions = sorted(hist["version"].tolist())
if len(versions) >= 2:
    a, b = st.columns(2)
    v_from = a.selectbox("Compare from version", versions, index=len(versions) - 2)
    v_to = b.selectbox("to version", versions, index=len(versions) - 1)
    if v_from != v_to:
        d = be.schema_diff(table, int(v_from), int(v_to))
        parts = [chip(f"+ {c}", "green") for c in d.get("added", [])]
        parts += [chip(f"− {c}", "red") for c in d.get("removed", [])]
        parts += [chip(f"{c}: {t}", "violet") for c, t in d.get("type_changed", {}).items()]
        drift = {c: r for c, r in (d.get("numeric_mean_ratio") or {}).items() if r and abs(r - 1) > 0.5}
        parts += [chip(f"{c} mean ×{r}", "amber") for c, r in drift.items()]
        st.markdown(" ".join(parts) or chip("no schema or value change", "slate"), unsafe_allow_html=True)
