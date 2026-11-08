# Databricks notebook source
# MAGIC %md
# MAGIC # Lakehouse Doctor · 01 · Try the doctor in a notebook
# MAGIC Lists recent failed runs and asks the doctor to diagnose each one. This is the core loop the
# MAGIC app wraps. Run `00_setup_and_break_things` first and wait for the chaos jobs to fail.

# COMMAND ----------

# MAGIC %pip install -q openai
# MAGIC %restart_python

# COMMAND ----------

import json, os, sys
sys.path.insert(0, os.path.abspath("../app"))
from doctor import signals
from doctor.agent import Doctor
from doctor.db import SparkSql

failures = signals.recent_failures(hours=24)
display(failures)

# COMMAND ----------

doc = Doctor(SparkSql(spark))
for f in failures[:3]:
    print("=" * 100, "\n", f["run_name"], f["run_id"])
    dx = doc.diagnose(f["run_id"])
    print(json.dumps(dx, indent=2))
    print("tool calls:", [t["tool"] for t in doc.trace])

# COMMAND ----------

# MAGIC %md ### Cost + slow queries from system tables (may lag behind real time)

# COMMAND ----------

db = SparkSql(spark)
display(signals.top_cost_drivers(db, days=7))
display(signals.slow_queries(db, days=1))
