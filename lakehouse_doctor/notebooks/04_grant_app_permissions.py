# Databricks notebook source
# MAGIC %md
# MAGIC # Lakehouse Doctor · 04 · Grant the app its permissions
# MAGIC A Databricks App runs as its **own service principal**, not as you. Without these grants it can't see
# MAGIC the chaos jobs, read notebooks, write the incident tables or read system tables.
# MAGIC Run this **after** creating the app. Every step prints ✔ or ✖ (with the reason) and keeps going.

# COMMAND ----------

dbutils.widgets.text("app_name", "lakehouse-doctor")
dbutils.widgets.text("warehouse_id", "7474644806266622")
APP, WAREHOUSE = dbutils.widgets.get("app_name"), dbutils.widgets.get("warehouse_id")

import os, posixpath, sys
sys.path.insert(0, os.path.abspath("../app"))
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs as J, sql as S, workspace as W
from doctor import config

w = WorkspaceClient()
sp = w.apps.get(APP).service_principal_client_id
print("app service principal:", sp)

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

# 3. SQL warehouse
step(f"CAN_USE on warehouse {WAREHOUSE}", lambda: w.warehouses.update_permissions(
    WAREHOUSE, access_control_list=[S.WarehouseAccessControlRequest(
        service_principal_name=sp, permission_level=S.WarehousePermissionLevel.CAN_USE)]))

# COMMAND ----------

# 4. Unity Catalog: the doctor schema (read/write + create tables) and system tables (read)
grants = [
    f"GRANT USE CATALOG ON CATALOG {config.CATALOG} TO `{sp}`",
    f"GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA {config.CATALOG}.{config.SCHEMA} TO `{sp}`",
    f"GRANT USE CATALOG ON CATALOG system TO `{sp}`",
]
for schema in ["lakeflow", "billing", "query", "access"]:
    grants.append(f"GRANT USE SCHEMA, SELECT ON SCHEMA system.{schema} TO `{sp}`")
for g in grants:
    step(g, lambda g=g: spark.sql(g))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Two grants to do by hand (UI)
# MAGIC - **Genie space** (if used): open the space → Share → add the app's service principal with *Can run*.
# MAGIC - **Model endpoint**: Serving → your chat model → Permissions → add the service principal with *Can query*
# MAGIC   (pay-per-token foundation models are usually queryable by everyone; skip if it already works).
# MAGIC
# MAGIC If a system-table grant fails with a permission error, you're not a metastore admin; the app still works,
# MAGIC except the **Costs** tab and job history.
