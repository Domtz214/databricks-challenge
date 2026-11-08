# Databricks notebook source
# MAGIC %md
# MAGIC # Lakehouse Doctor · 02 · The upstream-drift scenario (the differentiating demo)
# MAGIC A 3-job chain: `ingest_orders` → `orders_clean` → (`daily_revenue`, `customer_ltv`).
# MAGIC
# MAGIC 1. Healthy run of the whole chain (creates history + lineage).
# MAGIC 2. Ingest runs again in **drift** mode: it **succeeds**, but changes `o_totalprice`.
# MAGIC 3. `daily_revenue` fails (text mode) or goes silently 100× wrong (cents mode).
# MAGIC
# MAGIC Then: click **Diagnose error** on the failed run in the Jobs UI (built-in Assistant) and compare it
# MAGIC with the doctor (cell at the bottom, or the app). The built-in fix casts downstream; the doctor
# MAGIC should name `ingest_orders` + the table version that changed and list the blast radius.
# MAGIC Run `00_setup_and_break_things` first (it creates `workspace.doctor.orders`).

# COMMAND ----------

dbutils.widgets.dropdown("drift", "drift_type", ["drift_type", "drift_cents"])
DRIFT = dbutils.widgets.get("drift")

import os, posixpath, sys
sys.path.insert(0, os.path.abspath("../app"))
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import NotebookTask, Task
from doctor import config

w = WorkspaceClient()
nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
chaos_dir = posixpath.join(posixpath.dirname(posixpath.dirname(nb)), "chaos")
existing = {j.settings.name: j.job_id for j in w.jobs.list() if j.settings and j.settings.name}

def ensure_job(name, params=None):
    job_name = config.CHAOS_JOB_PREFIX + name
    if job_name in existing:
        return existing[job_name]
    task = Task(task_key="main", notebook_task=NotebookTask(
        notebook_path=posixpath.join(chaos_dir, name), base_parameters=params or {}))
    return w.jobs.create(name=job_name, tasks=[task]).job_id

ingest = ensure_job("chain_ingest_orders", {"mode": "good"})
revenue = ensure_job("chain_daily_revenue")
ltv = ensure_job("chain_customer_ltv")

def run(job_id, label, params=None):
    try:
        r = w.jobs.run_now(job_id, notebook_params=params or {}).result()
        print(f"{label}: {r.state.result_state.value}  run_id={r.run_id}")
        return r.run_id
    except Exception as e:
        print(f"{label}: FAILED ({str(e)[:150]})")

# COMMAND ----------

# MAGIC %md ### 1. Healthy chain

# COMMAND ----------

run(ingest, "ingest (good)", {"mode": "good"})
run(revenue, "daily_revenue")
run(ltv, "customer_ltv")

# COMMAND ----------

# MAGIC %md ### 2. Upstream drift (ingest succeeds) → 3. downstream breaks

# COMMAND ----------

run(ingest, f"ingest ({DRIFT})", {"mode": DRIFT})
run(revenue, "daily_revenue (after drift)")
run(ltv, "customer_ltv (after drift)")
display(spark.sql("DESCRIBE HISTORY workspace.doctor.orders_clean LIMIT 3"))

# COMMAND ----------

# MAGIC %md ### Ask the doctor about the newest failure
# MAGIC (In `drift_cents` mode nothing fails: open the app's Incidents tab after adding the value check,
# MAGIC or call `lineage.schema_diff` below to see the 100× mean ratio.)

# COMMAND ----------

# MAGIC %pip install -q openai

# COMMAND ----------

import json, os, sys
sys.path.insert(0, os.path.abspath("../app"))
from doctor import lineage, signals
from doctor.agent import Doctor
from doctor.db import SparkSql

db = SparkSql(spark)
print(json.dumps(lineage.schema_diff(db, "workspace.doctor.orders_clean"), indent=2, default=str))

fails = [f for f in signals.recent_failures(hours=2) if "chain_" in (f["run_name"] or "")]
if fails:
    doc = Doctor(db)
    print(json.dumps(doc.diagnose(fails[0]["run_id"]), indent=2))
    print("tools used:", [t["tool"] for t in doc.trace])
