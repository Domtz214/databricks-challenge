"""Demo data (DOCTOR_FAKE=1): lets the UI run anywhere and powers the UI tests.
The upstream-drift diagnosis is the doctor's real output from this workspace."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

NB = "/Users/demo/databricks-challenge/lakehouse_doctor/chaos/"
NOW = datetime.now(timezone.utc)

INGEST_SRC = '''# Databricks notebook source
dbutils.widgets.dropdown("mode", "good", ["good", "drift_type", "drift_cents"])
mode = dbutils.widgets.get("mode")

from pyspark.sql import functions as F

clean = spark.read.table("workspace.doctor.orders").select("o_orderkey", "o_custkey", "o_orderdate", "o_totalprice")
if mode == "drift_type":
    clean = clean.withColumn("o_totalprice", F.format_number("o_totalprice", 2))
elif mode == "drift_cents":
    clean = clean.withColumn("o_totalprice", (F.col("o_totalprice") * 100).cast("bigint"))

clean.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.doctor.orders_clean")
'''

DRIFT_DX = {
    "summary": "Revenue and LTV jobs broke because the upstream ingest job started writing o_totalprice as text.",
    "root_cause": "doctor-chaos-chain_ingest_orders (run with mode=drift_type) applied F.format_number to "
                  "o_totalprice, overwriting orders_clean v3 with a STRING column; SUM() downstream fails with CAST_INVALID_INPUT.",
    "category": "schema_drift", "fix_location": "upstream_job",
    "upstream_culprit": {"job_name": "doctor-chaos-chain_ingest_orders", "job_id": 674740504145445,
                         "table": "workspace.doctor.orders_clean", "version": 3,
                         "timestamp": "2026-11-08 16:05:22", "change": "o_totalprice decimal(18,2) -> string"},
    "blast_radius": ["workspace.doctor.customer_ltv", "workspace.doctor.daily_revenue_v2"],
    "evidence": ["run_error: [CAST_INVALID_INPUT] '1,234.50' cannot be cast to DOUBLE in SUM(o_totalprice)",
                 "schema_diff orders_clean v2 -> v3: o_totalprice decimal(18,2) -> string",
                 "table_history: v3 written by job doctor-chaos-chain_ingest_orders, run 228647290034810",
                 "run_details: run 228647290034810 parameters {mode: drift_type}",
                 "source_code chain_ingest_orders line 9: F.format_number(\"o_totalprice\", 2)"],
    "fix": {"type": "code_patch", "notebook_path": NB + "chain_ingest_orders",
            "description": "Stop formatting o_totalprice as text; keep it DECIMAL.",
            "before": 'clean = clean.withColumn("o_totalprice", F.format_number("o_totalprice", 2))',
            "after": 'clean = clean.withColumn("o_totalprice", F.col("o_totalprice").cast("decimal(18,2)"))'},
    "genie_code_prompt": "In chain_ingest_orders, remove the format_number conversion so o_totalprice stays "
                         "DECIMAL(18,2), and assert the output schema before writing orders_clean.",
    "prevention": "Add a schema contract check on orders_clean (o_totalprice must be DECIMAL) before the write.",
    "confidence": 0.9, "fix_verified": True,
    "verification": "fix.before found exactly once in " + NB + "chain_ingest_orders",
}
DRIFT_TRACE = [{"tool": t, "args": a, "result_preview": ""} for t, a in [
    ("run_error", '{"run_id": 228647290034810}'), ("source_code", '{"notebook_path": "chain_customer_ltv"}'),
    ("table_info", '{"full_name": "workspace.doctor.orders_clean"}'), ("lineage", '{"table": "workspace.doctor.orders_clean"}'),
    ("table_history", '{"table": "workspace.doctor.orders_clean"}'), ("schema_diff", '{"table": "workspace.doctor.orders_clean"}'),
    ("run_details", '{"run_id": 228647290034810}'), ("source_code", '{"notebook_path": "chain_ingest_orders"}')]]

TYPO_DX = {
    "summary": "Daily revenue job failed because it reads a table that doesn't exist (typo).",
    "root_cause": "chaos_typo_table line 5 reads workspace.doctor.ordres; the schema has `orders`.",
    "category": "missing_table", "fix_location": "this_job", "upstream_culprit": None, "blast_radius": [],
    "evidence": ["run_error: [TABLE_OR_VIEW_NOT_FOUND] workspace.doctor.ordres",
                 "table_info: ordres missing; tables_in_schema includes 'orders'"],
    "fix": {"type": "code_patch", "notebook_path": NB + "chaos_typo_table", "description": "Fix the table name.",
            "before": 'spark.read.table("workspace.doctor.ordres")', "after": 'spark.read.table("workspace.doctor.orders")'},
    "genie_code_prompt": "Replace the table name ordres with orders in chaos_typo_table.",
    "prevention": "Reference tables through a shared constants module.", "confidence": 0.97,
    "fix_verified": True, "verification": "fix.before found exactly once",
}

EDGES = [
    {"source": "workspace.doctor.orders", "target": "workspace.doctor.orders_clean", "job_id": 1, "job_name": "doctor-chaos-chain_ingest_orders", "notebook_path": NB + "chain_ingest_orders"},
    {"source": "workspace.doctor.orders_clean", "target": "workspace.doctor.daily_revenue_v2", "job_id": 2, "job_name": "doctor-chaos-chain_daily_revenue", "notebook_path": NB + "chain_daily_revenue"},
    {"source": "workspace.doctor.orders_clean", "target": "workspace.doctor.customer_ltv", "job_id": 3, "job_name": "doctor-chaos-chain_customer_ltv", "notebook_path": NB + "chain_customer_ltv"},
    {"source": "workspace.doctor.orders", "target": "workspace.doctor.daily_revenue", "job_id": 4, "job_name": "doctor-chaos-chaos_typo_table", "notebook_path": NB + "chaos_typo_table"},
    {"source": "workspace.doctor.orders", "target": "workspace.doctor.order_item_stats", "job_id": 5, "job_name": "doctor-chaos-chaos_bad_data", "notebook_path": NB + "chaos_bad_data"},
]


def _f(run_id, name, mins_ago, msg):
    return {"job_id": run_id // 1000, "run_id": run_id, "run_name": name, "result_state": "FAILED",
            "state_message": msg, "start_time": (NOW - timedelta(minutes=mins_ago)).isoformat(), "run_page_url": None}


class FakeBackend:
    mode = "demo"

    def __init__(self):
        self._failures = [
            _f(228647290034810, "doctor-chaos-chain_customer_ltv", 12, "[CAST_INVALID_INPUT] '1,234.50' cannot be cast"),
            _f(228647290034811, "doctor-chaos-chain_daily_revenue", 13, "[CAST_INVALID_INPUT] '1,234.50' cannot be cast"),
            _f(941920347064095, "doctor-chaos-chaos_typo_table", 95, "[TABLE_OR_VIEW_NOT_FOUND] workspace.doctor.ordres"),
            _f(551200000000001, "doctor-chaos-chaos_bad_data", 140, "[DIVIDE_BY_ZERO] Division by zero"),
        ]
        self._dx = {941920347064095: {"run_id": 941920347064095, "status": "fixed_and_rerun", "dx": TYPO_DX,
                                      "trace": DRIFT_TRACE[:3], "decided_by": "you"}}

    def clear_cache(self):
        pass

    def diagnoses(self):
        import json
        out = []
        for rid, d in self._dx.items():
            f = next(x for x in self._failures if x["run_id"] == rid)
            out.append({"run_id": rid, "job_id": f["job_id"], "run_name": f["run_name"], "failed_at": f["start_time"],
                        "diagnosed_at": NOW.isoformat(), "category": d["dx"]["category"], "summary": d["dx"]["summary"],
                        "confidence": d["dx"]["confidence"], "fix_verified": d["dx"]["fix_verified"],
                        "fix_location": d["dx"]["fix_location"], "diagnosis_json": json.dumps(d["dx"]),
                        "trace_json": json.dumps(d["trace"]), "status": d["status"],
                        "decided_at": NOW.isoformat() if d["status"] != "diagnosed" else None,
                        "decided_by": d.get("decided_by")})
        return out

    def save_diagnosis(self, f, dx, trace):
        self._dx[f["run_id"]] = {"dx": dx, "trace": trace, "status": "diagnosed"}

    def decide(self, f, dx, decision, user):
        self._dx.setdefault(f["run_id"], {"dx": dx, "trace": []})["status"] = decision

    def failures(self, hours=24):
        return list(self._failures)

    def run_trend(self, days=7):
        rows = []
        for i in range(days, -1, -1):
            day = (NOW - timedelta(days=i)).date().isoformat()
            rows += [{"day": day, "outcome": "success", "runs": 18 + (i * 7) % 9},
                     {"day": day, "outcome": "failed", "runs": [0, 1, 0, 2, 0, 1, 0, 4][i % 8]}]
        return rows

    def diagnose(self, run_id, on_event=None):
        trace = DRIFT_TRACE if "chain" in self._name(run_id) else DRIFT_TRACE[:3]
        for t in trace:
            time.sleep(0.05)
            (on_event or (lambda e: None))(t)
        return (DRIFT_DX if "chain" in self._name(run_id) else TYPO_DX) | {"run_id": run_id}, trace

    def _name(self, run_id):
        return next((f["run_name"] for f in self._failures if f["run_id"] == run_id), "")

    def notebook_source(self, path):
        return INGEST_SRC if "ingest" in path else 'df = spark.read.table("workspace.doctor.ordres")\n'

    def apply_fix(self, f, dx):
        return ["✔ Patched chain_ingest_orders (backup kept)", "✔ Re-ran upstream chain_ingest_orders: SUCCESS",
                f"✔ Repairing downstream run {f['run_id']}"]

    def rerun(self, run_id):
        pass

    def lineage_edges(self):
        return EDGES

    def table_history(self, table):
        return [{"version": v, "timestamp": str(NOW - timedelta(hours=4 - v)), "operation": "CREATE OR REPLACE TABLE AS SELECT",
                 "user": "demo@tamu.edu", "job_id": 1, "job_name": "doctor-chaos-chain_ingest_orders",
                 "job_run_id": 228647290034810 if v == 3 else 100 + v, "parameters": {"mode": "Overwrite"}}
                for v in range(3, -1, -1)]

    def schema_diff(self, table, a, b):
        changed = {"o_totalprice": "decimal(18,2) -> string"} if b == 3 else {}
        return {"table": table, "from_version": a, "to_version": b, "added": [], "removed": [],
                "type_changed": changed, "numeric_mean_ratio": {"o_custkey": 1.0, "o_orderkey": 1.0}}

    def cost_drivers(self, days=7):
        return [{"product": p, "resource_id": r, "dbus": d, "list_cost_usd": round(d * 0.7, 2)} for p, r, d in [
            ("SQL", "warehouse 7474644806266622", 41.2), ("JOBS", "doctor-chaos-chain_ingest_orders", 12.8),
            ("INTERACTIVE", "notebook 0177", 9.4), ("DLT", "triage-pipeline", 6.1), ("JOBS", "doctor-chaos-chaos_bad_data", 2.2)]]

    def slow_queries(self):
        return [{"statement_id": "01ef-a", "executed_by": "demo@tamu.edu", "total_duration_ms": 48211,
                 "read_bytes": 9.1e9, "produced_rows": 12,
                 "statement_text": "SELECT * FROM samples.tpch.lineitem l JOIN samples.tpch.orders o ON ... ORDER BY 1"},
                {"statement_id": "01ef-b", "executed_by": "demo@tamu.edu", "total_duration_ms": 12904,
                 "read_bytes": 1.2e9, "produced_rows": 4000, "statement_text": "SELECT o_custkey, SUM(o_totalprice) ..."}]

    def failure_cost(self, days=30):
        return [{"job_id": 1, "dbus": 3.4, "list_cost_usd": 2.38}, {"job_id": 5, "dbus": 1.1, "list_cost_usd": 0.77}]

    def chaos_jobs(self):
        return {"chain_ingest_orders": 1, "chain_daily_revenue": 2, "chain_customer_ltv": 3,
                "chaos_typo_table": 4, "chaos_schema_drift": 6, "chaos_bad_data": 5}

    def run_scenario(self, key, on_step=None):
        log = [f"▶ started scenario {key} (demo mode)"]
        (on_step or (lambda m: None))(log[0])
        return log

    def genie_enabled(self):
        return True

    def genie_ask(self, q, conv):
        return {"conversation_id": "demo", "text": "Here are the jobs with the most failures in the last 7 days.",
                "sql": "SELECT job_name, count(*) AS failures FROM workspace.doctor.v_job_runs WHERE result_state = 'FAILED' GROUP BY 1 ORDER BY 2 DESC",
                "columns": ["job_name", "failures"],
                "rows": [["doctor-chaos-chain_customer_ltv", 3], ["doctor-chaos-chaos_typo_table", 2]]}
