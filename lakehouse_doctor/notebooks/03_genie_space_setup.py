# Databricks notebook source
# MAGIC %md
# MAGIC # Lakehouse Doctor · 03 · Data for the Genie space
# MAGIC Genie works best on a small set of clean, well-described tables. This notebook writes:
# MAGIC - `doctor.lineage_edges`: the doctor's code-derived lineage graph (refresh any time)
# MAGIC - `doctor.v_job_runs`, `doctor.v_job_cost`: friendly views over system tables
# MAGIC - `doctor.incidents` already exists (the app logs every diagnosis + decision there)
# MAGIC
# MAGIC Then create the space (instructions in the last cell) and put its ID in `app/app.yaml`.

# COMMAND ----------

import os, sys
sys.path.insert(0, os.path.abspath("../app"))
import pandas as pd
from doctor import lineage
from doctor.config import fq

edges = lineage.build_code_lineage()
pdf = pd.DataFrame(edges, columns=["source", "target", "job_id", "job_name", "notebook_path"])
spark.createDataFrame(pdf.astype({"job_id": "int64"}).fillna("")).write.mode("overwrite").saveAsTable(fq("lineage_edges"))
spark.sql(f"COMMENT ON TABLE {fq('lineage_edges')} IS 'Table-level lineage parsed from job notebook code: source table -> target table via job.'")
display(spark.table(fq("lineage_edges")))

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {fq('v_job_runs')} COMMENT 'One row per job run: job name, start/end, outcome.' AS
SELECT r.job_id, j.name AS job_name, r.run_id, r.period_start_time AS started_at, r.period_end_time AS ended_at,
       r.result_state, r.termination_code
FROM system.lakeflow.job_run_timeline r
LEFT JOIN (SELECT job_id, max_by(name, change_time) AS name FROM system.lakeflow.jobs GROUP BY job_id) j USING (job_id)
WHERE r.result_state IS NOT NULL
""")
spark.sql(f"""
CREATE OR REPLACE VIEW {fq('v_job_cost')} COMMENT 'Daily DBUs and list cost per job.' AS
SELECT u.usage_date, u.usage_metadata.job_id AS job_id, sum(u.usage_quantity) AS dbus,
       sum(u.usage_quantity * p.pricing.default) AS list_cost_usd
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
  ON u.sku_name = p.sku_name AND u.usage_start_time >= p.price_start_time
 AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.usage_metadata.job_id IS NOT NULL
GROUP BY ALL
""")
display(spark.sql(f"SELECT * FROM {fq('v_job_runs')} ORDER BY started_at DESC LIMIT 20"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Create the Genie space (SQL → Genie → New)
# MAGIC **Tables:** `workspace.doctor.incidents`, `workspace.doctor.lineage_edges`, `workspace.doctor.v_job_runs`,
# MAGIC `workspace.doctor.v_job_cost`, `workspace.doctor.orders_clean`
# MAGIC
# MAGIC **Instructions:**
# MAGIC ```
# MAGIC This space describes the reliability and cost of Databricks jobs in this workspace.
# MAGIC - incidents: one row per diagnosis by Lakehouse Doctor (category, summary, decision).
# MAGIC - lineage_edges: source table -> target table via a job. "Downstream of X" = follow source = X.
# MAGIC - v_job_runs: run outcomes; failed = result_state IN ('FAILED','TIMEDOUT','INTERNAL_ERROR').
# MAGIC - v_job_cost: DBUs and list cost per job per day. "Wasted cost" = cost on days/jobs with failed runs.
# MAGIC - orders_clean.o_totalprice is an order amount in US dollars.
# MAGIC ```
# MAGIC **Sample questions:** Which jobs failed most this week? · What is downstream of workspace.doctor.orders_clean?
# MAGIC · Average o_totalprice per day for the last 7 days · How many incidents were fixed vs dismissed?
# MAGIC
# MAGIC Copy the space ID from its URL (`.../genie/rooms/<ID>`) into `DOCTOR_GENIE_SPACE_ID` in `app/app.yaml`
# MAGIC (and `os.environ["DOCTOR_GENIE_SPACE_ID"] = "<ID>"` before creating a Doctor in notebooks).
