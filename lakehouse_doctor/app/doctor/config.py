import os

CATALOG = os.getenv("DOCTOR_CATALOG", "workspace")
SCHEMA = os.getenv("DOCTOR_SCHEMA", "doctor")
LLM_ENDPOINT = os.getenv("DOCTOR_LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
CHAOS_JOB_PREFIX = "doctor-chaos-"


def fq(name: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{name}"


# System tables, read through owner's-rights views in the doctor schema (created by notebook 04).
# The app's service principal can't be granted system.* on workspaces where you aren't a metastore
# admin, but it can read views you own. Set DOCTOR_SYSTEM_DIRECT=1 to query system.* directly instead.
_SYSTEM = {
    "job_run_timeline": "system.lakeflow.job_run_timeline",
    "jobs": "system.lakeflow.jobs",
    "billing_usage": "system.billing.usage",
    "list_prices": "system.billing.list_prices",
    "query_history": "system.query.history",
    "table_lineage": "system.access.table_lineage",
}


def sys_table(key: str) -> str:
    if os.getenv("DOCTOR_SYSTEM_DIRECT") == "1":
        return _SYSTEM[key]
    return fq(f"sys_{key}")
