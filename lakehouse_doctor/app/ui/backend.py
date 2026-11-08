"""Everything the pages need, behind one interface.

RealBackend talks to the workspace (Jobs API, SQL warehouse, Foundation Model API, Genie).
FakeBackend (DOCTOR_FAKE=1) serves canned data so the UI can be previewed and tested anywhere.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import streamlit as st

from doctor import config
from doctor.config import fq, sys_table

SCENARIOS = {
    "typo": {"title": "Typo in a table name", "icon": "🔤",
             "story": "A job reads `ordres` instead of `orders`.", "expect": "missing_table · fix in place"},
    "schema_append": {"title": "Bad append", "icon": "🧱",
                      "story": "A feed appends prices as text into a DECIMAL column.", "expect": "schema mismatch · fix in place"},
    "bad_data": {"title": "Bad data, good code", "icon": "🧪",
                 "story": "Some orders have 0 items → divide by zero.", "expect": "data_quality · guard + expectation"},
    "upstream_drift": {"title": "Silent upstream drift", "icon": "🌊",
                       "story": "Ingest *succeeds* but turns prices into text; revenue + LTV break downstream.",
                       "expect": "schema_drift · fix UPSTREAM · blast radius 2"},
    "reset": {"title": "Reset the chain", "icon": "♻️",
              "story": "Re-run ingest in good mode so the chain is healthy again.", "expect": "all green"},
}


def _ttl(seconds: int):
    """Tiny per-instance TTL cache (avoids hashing `self` in st.cache_data)."""
    def deco(fn):
        def wrapper(self, *args, **kwargs):
            key = (fn.__name__, args, tuple(sorted(kwargs.items())))
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < seconds:
                return hit[1]
            val = fn(self, *args, **kwargs)
            self._cache[key] = (time.time(), val)
            return val
        return wrapper
    return deco


class RealBackend:
    mode = "live"

    def __init__(self):
        from doctor.db import WarehouseSql

        self.db = WarehouseSql()
        self._cache: dict = {}
        self._shared: set = set()
        self.ensure_tables()

    def clear_cache(self):
        self._cache.clear()

    # ---------- storage ----------
    def ensure_tables(self):
        try:
            self.db.execute(f"""CREATE TABLE IF NOT EXISTS {fq('diagnoses')} (
                run_id BIGINT, job_id BIGINT, run_name STRING, failed_at TIMESTAMP, diagnosed_at TIMESTAMP,
                category STRING, summary STRING, confidence DOUBLE, fix_verified BOOLEAN, fix_location STRING,
                diagnosis_json STRING, trace_json STRING)""")
            self.db.execute(f"""CREATE TABLE IF NOT EXISTS {fq('incidents')} (
                run_id BIGINT, job_id BIGINT, run_name STRING, category STRING, summary STRING,
                diagnosis_json STRING, decision STRING, decided_by STRING, decided_at TIMESTAMP)""")
        except Exception as e:  # app SP may lack CREATE; setup notebook creates them too
            print("ensure_tables:", e)

    def share_tables_with(self, user: str) -> None:
        """If the app created these tables, its service principal owns them and humans can't read them
        (e.g. in Genie). Grant the signed-in user read access. No-op/ignored when the user already owns them."""
        if not user or "@" not in user or user in self._shared:
            return
        for t in ("diagnoses", "incidents"):
            try:
                self.db.execute(f"GRANT SELECT ON TABLE {fq(t)} TO `{user}`")
            except Exception as e:
                print(f"share {t} with {user}: {str(e)[:120]}")
        self._shared.add(user)

    @_ttl(15)
    def diagnoses(self) -> list[dict]:
        return self.db.query(f"""
            WITH d AS (SELECT * FROM {fq('diagnoses')}
                       QUALIFY row_number() OVER (PARTITION BY run_id ORDER BY diagnosed_at DESC) = 1),
                 i AS (SELECT run_id, decision, decided_at, decided_by FROM {fq('incidents')}
                       QUALIFY row_number() OVER (PARTITION BY run_id ORDER BY decided_at DESC) = 1)
            SELECT d.*, coalesce(i.decision, 'diagnosed') AS status, i.decided_at, i.decided_by
            FROM d LEFT JOIN i USING (run_id) ORDER BY d.diagnosed_at DESC""")

    def save_diagnosis(self, f: dict, dx: dict, trace: list) -> None:
        self.db.execute(
            f"""INSERT INTO {fq('diagnoses')} VALUES (:run_id, :job_id, :run_name, CAST(:failed_at AS TIMESTAMP),
                current_timestamp(), :category, :summary, :confidence, :verified, :loc, :dx, :trace)""",
            {"run_id": f["run_id"], "job_id": f["job_id"], "run_name": f["run_name"], "failed_at": f["start_time"],
             "category": dx.get("category"), "summary": dx.get("summary"),
             "confidence": float(dx.get("confidence") or 0), "verified": bool(dx.get("fix_verified")),
             "loc": dx.get("fix_location"), "dx": json.dumps(dx), "trace": json.dumps(trace)})
        self.clear_cache()

    def decide(self, f: dict, dx: dict, decision: str, user: str) -> None:
        self.db.execute(
            f"""INSERT INTO {fq('incidents')} VALUES (:run_id, :job_id, :run_name, :category, :summary, :dx,
                :decision, :user, current_timestamp())""",
            {"run_id": f["run_id"], "job_id": f["job_id"], "run_name": f["run_name"], "category": dx.get("category"),
             "summary": dx.get("summary"), "dx": json.dumps(dx), "decision": decision, "user": user})
        self.clear_cache()

    # ---------- live signals ----------
    @_ttl(20)
    def failures(self, hours: int = 24) -> list[dict]:
        from doctor import signals
        return signals.recent_failures(hours=hours)

    @_ttl(60)
    def run_trend(self, days: int = 7) -> list[dict]:
        """Daily success/failure counts from the Jobs API (real-time, unlike system tables)."""
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        counts: Counter = Counter()
        for i, r in enumerate(w.jobs.list_runs(completed_only=True, start_time_from=since, limit=25)):
            if i >= 500:
                break
            day = datetime.fromtimestamp(r.start_time / 1000, timezone.utc).date().isoformat()
            state = r.state.result_state.value if r.state and r.state.result_state else "UNKNOWN"
            counts[(day, "success" if state == "SUCCESS" else "failed")] += 1
        return [{"day": d, "outcome": o, "runs": n} for (d, o), n in sorted(counts.items())]

    # ---------- doctor ----------
    def diagnose(self, run_id: int, on_event=None) -> tuple[dict, list]:
        from doctor.agent import Doctor
        doc = Doctor(self.db)
        doc._edges = self.lineage_edges()
        dx = doc.diagnose(run_id, on_event=on_event)
        return dx, list(doc.trace)

    def notebook_source(self, path: str) -> str:
        from doctor import signals
        return signals.raw_source(path)

    def apply_fix(self, f: dict, dx: dict) -> list[str]:
        from doctor import signals
        fix, culprit, log = dx.get("fix") or {}, dx.get("upstream_culprit") or {}, []
        res = signals.apply_patch(fix.get("notebook_path"), fix.get("before"), fix.get("after"))
        if not res.get("applied"):
            return [f"✖ Patch not applied: {res.get('reason')}"]
        log.append(f"✔ Patched {fix.get('notebook_path')} (backup {res['backup'].split('/')[-1]})")
        if dx.get("fix_location") == "upstream_job" and culprit.get("job_id"):
            state = signals.run_job_and_wait(int(culprit["job_id"]))
            log.append(f"{'✔' if state == 'SUCCESS' else '✖'} Re-ran upstream {culprit.get('job_name')}: {state}")
        signals.rerun(f["run_id"])
        log.append(f"✔ Repairing downstream run {f['run_id']} (failed tasks only)")
        return log

    def rerun(self, run_id: int) -> None:
        from doctor import signals
        signals.rerun(run_id)

    # ---------- lineage ----------
    @_ttl(300)
    def lineage_edges(self) -> list[dict]:
        from doctor import lineage
        return lineage.build_code_lineage()

    def table_history(self, table: str) -> list[dict]:
        from doctor import lineage
        return lineage.table_history(self.db, table, 15)

    def schema_diff(self, table: str, a: int, b: int) -> dict:
        from doctor import lineage
        return lineage.schema_diff(self.db, table, a, b)

    # ---------- cost ----------
    @_ttl(300)
    def cost_drivers(self, days: int = 7) -> list[dict]:
        from doctor import signals
        return signals.top_cost_drivers(self.db, days=days, n=15)

    @_ttl(300)
    def slow_queries(self) -> list[dict]:
        from doctor import signals
        return signals.slow_queries(self.db, days=1, n=10)

    @_ttl(300)
    def failure_cost(self, days: int = 30) -> list[dict]:
        """DBUs + list $ spent on runs that FAILED (billing joined to run outcomes). Lags a few hours."""
        return self.db.query(f"""
            WITH failed AS (SELECT DISTINCT CAST(run_id AS STRING) AS run_id, job_id FROM {sys_table('job_run_timeline')}
                            WHERE result_state IN ('FAILED','TIMEDOUT','INTERNAL_ERROR')
                              AND period_start_time >= current_date() - INTERVAL {int(days)} DAYS)
            SELECT f.job_id, round(sum(u.usage_quantity), 3) AS dbus,
                   round(sum(u.usage_quantity * p.pricing.default), 2) AS list_cost_usd
            FROM {sys_table('billing_usage')} u
            JOIN failed f ON u.usage_metadata.job_run_id = f.run_id
            LEFT JOIN {sys_table('list_prices')} p ON u.sku_name = p.sku_name
             AND u.usage_start_time >= p.price_start_time
             AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
            GROUP BY f.job_id ORDER BY dbus DESC""")

    # ---------- chaos lab ----------
    @_ttl(60)
    def chaos_jobs(self) -> dict:
        from databricks.sdk import WorkspaceClient
        return {j.settings.name.replace(config.CHAOS_JOB_PREFIX, ""): j.job_id
                for j in WorkspaceClient().jobs.list() if j.settings and (j.settings.name or "").startswith(config.CHAOS_JOB_PREFIX)}

    def run_scenario(self, key: str, on_step=None) -> list[str]:
        from databricks.sdk import WorkspaceClient
        w, jobs, log = WorkspaceClient(), self.chaos_jobs(), []
        step = on_step or (lambda m: None)

        def go(name, params=None, wait=False):
            if name not in jobs:
                msg = f"✖ job {config.CHAOS_JOB_PREFIX}{name} not found (run notebooks 00 and 02 once)"
                log.append(msg); step(msg); return None
            waiter = w.jobs.run_now(jobs[name], notebook_params=params or {})
            if not wait:
                msg = f"▶ started {name}"
            else:
                try:
                    r = waiter.result(timeout=timedelta(minutes=15))
                    msg = f"✔ {name}: {r.state.result_state.value}"
                except Exception:
                    msg = f"✖ {name}: FAILED"
            log.append(msg); step(msg)

        if key == "typo":
            go("chaos_typo_table")
        elif key == "schema_append":
            go("chaos_schema_drift")
        elif key == "bad_data":
            go("chaos_bad_data")
        elif key == "upstream_drift":
            go("chain_ingest_orders", {"mode": "drift_type"}, wait=True)
            go("chain_daily_revenue"); go("chain_customer_ltv")
        elif key == "reset":
            go("chain_ingest_orders", {"mode": "good"}, wait=True)
            go("chain_daily_revenue"); go("chain_customer_ltv")
        self.clear_cache()
        return log

    # ---------- genie ----------
    def genie_enabled(self) -> bool:
        from doctor import genie
        return genie.enabled()

    def genie_ask(self, q: str, conv: str | None) -> dict:
        from doctor import genie
        return genie.ask(q, conv)


@st.cache_resource
def get_backend():
    if os.getenv("DOCTOR_FAKE") == "1":
        from ui.fake import FakeBackend
        return FakeBackend()
    return RealBackend()


def current_user() -> str:
    try:
        return st.context.headers.get("X-Forwarded-Email") or "you"
    except Exception:
        return "you"


def incidents(backend, hours: int = 72) -> list[dict]:
    """Failures (live) merged with stored diagnoses + decisions → one list for the UI."""
    dx_by_run = {int(d["run_id"]): d for d in backend.diagnoses()}
    out, seen = [], set()
    for f in backend.failures(hours):
        d = dx_by_run.get(int(f["run_id"]))
        out.append(_merge(f, d)); seen.add(int(f["run_id"]))
    for rid, d in dx_by_run.items():  # diagnosed runs that fell out of the live window
        if rid not in seen:
            out.append(_merge({"run_id": rid, "job_id": d.get("job_id"), "run_name": d.get("run_name"),
                               "start_time": str(d.get("failed_at")), "result_state": "FAILED",
                               "state_message": "", "run_page_url": None}, d))
    return out


def _merge(f: dict, d: dict | None) -> dict:
    row = dict(f)
    row["status"] = d["status"] if d else "new"
    row["dx"] = json.loads(d["diagnosis_json"]) if d and d.get("diagnosis_json") else None
    row["trace"] = json.loads(d["trace_json"]) if d and d.get("trace_json") else []
    return row
