# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup
# MAGIC Creates the schema, volumes, and the empty tables the pipeline/app expect, then drops a few
# MAGIC synthetic reports into the landing zone so you can run the pipeline end-to-end **immediately**.
# MAGIC
# MAGIC Run once. Safe to re-run (everything is `IF NOT EXISTS`).

# COMMAND ----------

import os, sys
sys.path.append(os.path.abspath("../src"))

from triage import config
from triage.config import fq
print(config.CATALOG, config.SCHEMA)

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {config.CATALOG}.{config.SCHEMA}")
for vol in ["landing", "reference", "photos"]:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {config.CATALOG}.{config.SCHEMA}.{vol}")

for d in [config.LANDING_REPORTS, config.LANDING_ALERTS, config.LANDING_GAUGES,
          f"{config.REFERENCE_DIR}/humaid", f"{config.REFERENCE_DIR}/svi", f"{config.REFERENCE_DIR}/census",
          f"{config.REFERENCE_DIR}/facilities", f"{config.REFERENCE_DIR}/guidance"]:
    dbutils.fs.mkdirs(d)

# COMMAND ----------

# MAGIC %md ### Tables written outside the pipeline (pipeline reads them, so they must exist)

# COMMAND ----------

ddl = {
    "dispatch_decisions": """incident_id STRING, decision STRING, resource STRING, notes STRING,
                             agent_json STRING, decided_by STRING, decided_at TIMESTAMP""",
    "ref_gazetteer": "name STRING, lat DOUBLE, lon DOUBLE, kind STRING",
    "ref_h3_area": "h3_area STRING, county_fips STRING, tract_geoid STRING, svi_percentile DOUBLE",
    "ref_gauge_flood_stage": "site_id STRING, flood_stage_ft DOUBLE, source STRING",
    "ref_facilities": """facility_id STRING, name STRING, kind STRING, address STRING, lat DOUBLE, lon DOUBLE,
                         capacity INT, is_open BOOLEAN, h3_area STRING""",
    "forecast_rescue_need": """h3_area STRING, forecast_for TIMESTAMP, horizon_h INT, risk DOUBLE,
                               model_version STRING, created_at TIMESTAMP""",
    "backtest_results": "run_id STRING, metric STRING, policy STRING, capacity_per_hour DOUBLE, value DOUBLE, created_at TIMESTAMP",
    "facility_lead_times": "facility_id STRING, name STRING, kind STRING, h3_area STRING, first_flagged_at TIMESTAMP, first_report_at TIMESTAMP, lead_time_h DOUBLE",
}
for name, cols in ddl.items():
    spark.sql(f"CREATE TABLE IF NOT EXISTS {fq(name)} ({cols})")

# Vector Search delta-sync needs Change Data Feed on the source table.
spark.sql(f"""CREATE TABLE IF NOT EXISTS {fq('guidance_chunks')}
              (chunk_id STRING, source STRING, page INT, text STRING, embedding ARRAY<FLOAT>)
              TBLPROPERTIES (delta.enableChangeDataFeed = true)""")

display(spark.sql(f"SHOW TABLES IN {config.CATALOG}.{config.SCHEMA}"))

# COMMAND ----------

# MAGIC %md ### Smoke test: 60 synthetic bilingual reports → landing zone
# MAGIC Then run the pipeline (Jobs & Pipelines → `triage-pipeline` → Start) and check `gold_priority_queue`.

# COMMAND ----------

import json, uuid
from triage.replay import synthetic_reports

rows = synthetic_reports(60, center=config.DEFAULT_CENTER, seed=7)
path = f"{config.LANDING_REPORTS}/synthetic_{uuid.uuid4().hex[:8]}.json"
with open(path, "w") as f:
    f.write("\n".join(json.dumps(r) for r in rows))
print("wrote", path)
