# Databricks notebook source
# MAGIC %md
# MAGIC # Lakehouse Doctor · 04 · Grant the app its permissions
# MAGIC A Databricks App runs as its **own service principal**, not as you. Without these grants it can't see
# MAGIC the chaos jobs, read notebooks, write the incident tables or read system tables.
# MAGIC Run this **after** creating the app. Every step prints ✔ or ✖ (with the reason) and keeps going.

# COMMAND ----------

dbutils.widgets.text("app_name", "lakehouse-doctor")
dbutils.widgets.dropdown("system_mode", "views", ["views", "copies"])
APP, SYSTEM_MODE = dbutils.widgets.get("app_name"), dbutils.widgets.get("system_mode")

import os, posixpath, sys
sys.path.insert(0, os.path.abspath("../app"))
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs as J, sql as S, workspace as W
from doctor import config

w = WorkspaceClient()
app = w.apps.get(APP)
sp = app.service_principal_client_id
# The warehouse attached under the app's Resources (16 hex chars, not the workspace id).
WAREHOUSE = next((r.sql_warehouse.id for r in (app.resources or []) if getattr(r, "sql_warehouse", None)), None)
print("app service principal:", sp, "| warehouse:", WAREHOUSE)

def step(label, fn):
    try:
        fn(); print("✔", label)
    except Exception as e:
        print("✖", label, "→", str(e)[:200])

# COMMAND ----------

# 1. Jobs: see runs, read errors, rerun/repair, trigger chaos scenarios
for j in w.jobs.list():
    if j.settings and (j.settings.name or "").startswith(config.CHAOS_JOB_PREFIX):
        step(f"CAN_MANAGE_RUN on job {j.settings.name}", lambda j=j: w.jobs.update_permissions(
            str(j.job_id), access_control_list=[J.JobAccessControlRequest(
                service_principal_name=sp, permission_level=J.JobPermissionLevel.CAN_MANAGE_RUN)]))

# 2. Notebooks: read source (diagnose) + edit (apply fix). Scope: the lakehouse_doctor folder only.
nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
folder = posixpath.dirname(posixpath.dirname(nb))
step(f"CAN_EDIT on {folder}", lambda: w.workspace.update_permissions(
    "directories", str(w.workspace.get_status(folder).object_id),
    access_control_list=[W.WorkspaceObjectAccessControlRequest(
        service_principal_name=sp, permission_level=W.WorkspaceObjectPermissionLevel.CAN_EDIT)]))

# 3. SQL warehouse (adding it under the app's Resources usually grants this already)
if WAREHOUSE:
    step(f"CAN_USE on warehouse {WAREHOUSE}", lambda: w.warehouses.update_permissions(
        WAREHOUSE, access_control_list=[S.WarehouseAccessControlRequest(
            service_principal_name=sp, permission_level=S.WarehousePermissionLevel.CAN_USE)]))
else:
    print("✖ no SQL warehouse resource on the app: add one (key 'sql-warehouse') and redeploy")

# COMMAND ----------

# 4. Unity Catalog: the doctor schema (read/write + create tables)
for g in [f"GRANT USE CATALOG ON CATALOG {config.CATALOG} TO `{sp}`",
          f"GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA {config.CATALOG}.{config.SCHEMA} TO `{sp}`"]:
    step(g, lambda g=g: spark.sql(g))

# COMMAND ----------

# MAGIC %md
# MAGIC ### 5. System tables → the app, without admin rights
# MAGIC Granting `system.*` needs a metastore admin. Instead, expose what the app needs through objects **you own**
# MAGIC in `workspace.doctor` (`sys_*`). The app already has SELECT on that schema.
# MAGIC - `views` (default): owner's-rights views, always live. Unity Catalog checks the underlying system tables
# MAGIC   against the view owner (you).
# MAGIC - `copies`: if the app still can't read the views, switch the widget to `copies`. That materializes snapshot
# MAGIC   tables; schedule this notebook (or just this cell) hourly to keep them fresh.

# COMMAND ----------

from doctor.config import _SYSTEM, fq

LOOKBACK = {"billing_usage": "usage_date >= current_date() - INTERVAL 45 DAYS",
            "query_history": "start_time >= current_timestamp() - INTERVAL 7 DAYS",
            "job_run_timeline": "period_start_time >= current_timestamp() - INTERVAL 45 DAYS",
            "table_lineage": "event_time >= current_timestamp() - INTERVAL 45 DAYS"}
for key, src in _SYSTEM.items():
    where = f" WHERE {LOOKBACK[key]}" if key in LOOKBACK else ""
    target = fq(f"sys_{key}")
    if SYSTEM_MODE == "views":
        sql = f"CREATE OR REPLACE VIEW {target} AS SELECT * FROM {src}{where}"
    else:
        sql = f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {src}{where}"
    step(f"{'view' if SYSTEM_MODE == 'views' else 'copy'} {target}", lambda sql=sql: spark.sql(sql))
    step(f"  readable: {target}", lambda t=target: spark.sql(f"SELECT 1 FROM {t} LIMIT 1").collect())

# COMMAND ----------

# MAGIC %md
# MAGIC ### Two grants to do by hand (UI)
# MAGIC - **Genie space** (if used): open the space → Share → add the app's service principal with *Can run*.
# MAGIC - **Model endpoint**: Serving → your chat model → Permissions → add the service principal with *Can query*
# MAGIC   (pay-per-token foundation models are usually queryable by everyone; skip if it already works).
# MAGIC
# MAGIC Then **redeploy the app** (Compute → Apps → lakehouse-doctor → Deploy) so it picks up the new code.
