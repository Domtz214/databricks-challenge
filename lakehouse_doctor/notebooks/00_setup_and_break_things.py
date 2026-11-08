# Databricks notebook source
# MAGIC %md
# MAGIC # Lakehouse Doctor · 00 · Setup + break things
# MAGIC Creates a small `workspace.doctor` schema with sample data, an `incidents` log, and three
# MAGIC **chaos jobs** that fail in realistic, different ways. Then runs them so the doctor has patients.

# COMMAND ----------

import os, posixpath, sys
sys.path.insert(0, os.path.abspath("../app"))
from doctor import config
from doctor.config import fq

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {config.CATALOG}.{config.SCHEMA}")
spark.sql(f"""CREATE OR REPLACE TABLE {fq('orders')} AS
              SELECT o_orderkey, o_custkey, o_orderdate, o_totalprice, o_orderstatus
              FROM samples.tpch.orders LIMIT 5000""")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {fq('incidents')} (
                run_id BIGINT, job_id BIGINT, run_name STRING, category STRING, summary STRING,
                diagnosis_json STRING, decision STRING, decided_by STRING, decided_at TIMESTAMP)""")
display(spark.sql(f"SHOW TABLES IN {config.CATALOG}.{config.SCHEMA}"))

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import NotebookTask, Task

w = WorkspaceClient()
nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
chaos_dir = posixpath.join(posixpath.dirname(posixpath.dirname(nb)), "chaos")

job_ids = {}
existing = {j.settings.name: j.job_id for j in w.jobs.list(name=None) if j.settings and j.settings.name}
for name in ["chaos_typo_table", "chaos_schema_drift", "chaos_bad_data"]:
    job_name = config.CHAOS_JOB_PREFIX + name
    if job_name in existing:
        job_ids[name] = existing[job_name]
        continue
    # No cluster spec -> serverless jobs compute.
    j = w.jobs.create(name=job_name, tasks=[Task(task_key="main",
                      notebook_task=NotebookTask(notebook_path=posixpath.join(chaos_dir, name)))])
    job_ids[name] = j.job_id
print(job_ids)

# COMMAND ----------

# MAGIC %md Run them. Each should FAIL within a minute or two (that's the point).

# COMMAND ----------

runs = {name: w.jobs.run_now(job_id).run_id for name, job_id in job_ids.items()}
print("started runs:", runs, "\nWatch them in Jobs & Pipelines -> Runs.")
