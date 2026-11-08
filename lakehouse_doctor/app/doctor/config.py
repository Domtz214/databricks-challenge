import os

CATALOG = os.getenv("DOCTOR_CATALOG", "workspace")
SCHEMA = os.getenv("DOCTOR_SCHEMA", "doctor")
LLM_ENDPOINT = os.getenv("DOCTOR_LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
CHAOS_JOB_PREFIX = "doctor-chaos-"


def fq(name: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{name}"
