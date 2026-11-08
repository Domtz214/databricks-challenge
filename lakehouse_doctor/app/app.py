"""Lakehouse Doctor: Databricks App (Streamlit)."""

import json

import pandas as pd
import streamlit as st

from doctor import config, signals
from doctor.agent import Doctor
from doctor.db import WarehouseSql

st.set_page_config(page_title="Lakehouse Doctor", page_icon="🩺", layout="wide")
st.title("🩺 Lakehouse Doctor")
st.caption("Finds what broke in your workspace, explains why, and fixes it with your approval.")


@st.cache_resource
def db():
    return WarehouseSql()


@st.cache_resource
def doctor():
    return Doctor(db())


def log_decision(f: dict, dx: dict, decision: str):
    user = st.context.headers.get("X-Forwarded-Email", "unknown")
    db().execute(
        f"""INSERT INTO {config.fq('incidents')} VALUES
            (:run_id, :job_id, :run_name, :category, :summary, :dx, :decision, :user, current_timestamp())""",
        {"run_id": f["run_id"], "job_id": f["job_id"], "run_name": f["run_name"],
         "category": dx.get("category"), "summary": dx.get("summary"), "dx": json.dumps(dx),
         "decision": decision, "user": user},
    )


incidents_tab, cost_tab, history_tab = st.tabs(["🚨 Incidents", "💸 Cost", "📜 History"])

with incidents_tab:
    hours = st.slider("Look back (hours)", 1, 72, 24)
    failures = signals.recent_failures(hours=hours)
    if not failures:
        st.success("No failed runs. Healthy lakehouse. (Run the chaos jobs to create patients.)")
    for f in failures:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{f['run_name']}** · `{f['result_state']}` · {f['start_time'][:19]}")
            c1.caption(f["state_message"])
            key = f"dx_{f['run_id']}"
            if c2.button("🩺 Diagnose", key=f"btn_{f['run_id']}"):
                with st.spinner("Reading the error, the code, the table metadata and the job history…"):
                    st.session_state[key] = doctor().diagnose(f["run_id"])
                    st.session_state[key + "_trace"] = list(doctor().trace)
            dx = st.session_state.get(key)
            if not dx:
                continue
            if "error" in dx:
                st.error(dx); continue

            st.markdown(f"### {dx.get('summary')}")
            st.markdown(f"**Root cause** ({dx.get('category')}, confidence {dx.get('confidence')}): {dx.get('root_cause')}")
            for e in dx.get("evidence", []):
                st.markdown(f"- {e}")
            fix = dx.get("fix") or {}
            st.markdown(f"**Proposed fix:** {fix.get('description')}")
            if fix.get("before") and fix.get("after"):
                a, b = st.columns(2)
                a.code(fix["before"], language="python"); a.caption("before")
                b.code(fix["after"], language="python"); b.caption("after")
            st.caption(f"Prevention: {dx.get('prevention')}")
            with st.expander("How the doctor investigated"):
                st.json(st.session_state.get(key + "_trace", []))

            approve, rerun_only, dismiss = st.columns(3)
            if approve.button("✅ Apply fix + rerun", key=f"apply_{f['run_id']}",
                              disabled=not (fix.get("before") and fix.get("after"))):
                err = signals.run_error(f["run_id"])
                path = next((t["notebook_path"] for t in err["failed_tasks"] if t["notebook_path"]), None)
                res = signals.apply_patch(path, fix["before"], fix["after"]) if path else {"applied": False}
                if res.get("applied"):
                    signals.rerun(f["run_id"])
                    log_decision(f, dx, "fixed_and_rerun")
                    st.success(f"Patched (backup: {res['backup']}) and rerunning. Check Runs in ~1 min.")
                else:
                    st.warning(f"Not applied: {res}")
            if rerun_only.button("🔁 Just rerun", key=f"rerun_{f['run_id']}"):
                signals.rerun(f["run_id"]); log_decision(f, dx, "rerun"); st.info("Rerunning.")
            if dismiss.button("✖️ Dismiss", key=f"dismiss_{f['run_id']}"):
                log_decision(f, dx, "dismissed"); st.info("Logged.")

with cost_tab:
    st.caption("From system.billing (can lag by a few hours).")
    try:
        cost = pd.DataFrame(signals.top_cost_drivers(db(), days=st.slider("Days", 1, 30, 7)))
        st.dataframe(cost, hide_index=True, use_container_width=True)
        slow = pd.DataFrame(signals.slow_queries(db(), days=1))
        st.subheader("Slowest queries (24h)")
        st.dataframe(slow, hide_index=True, use_container_width=True)
        # TODO(next): "Optimize" button -> doctor rewrites the query, runs both, reports speedup + $ saved.
    except Exception as e:
        st.warning(f"System tables not readable by the app: {e}")

with history_tab:
    try:
        st.dataframe(pd.DataFrame(db().query(
            f"SELECT decided_at, run_name, category, summary, decision, decided_by FROM {config.fq('incidents')} "
            "ORDER BY decided_at DESC LIMIT 200")), hide_index=True, use_container_width=True)
    except Exception as e:
        st.warning(str(e))
