"""Central configuration. Every value can be overridden with an env var
(Databricks Apps: set them in app/app.yaml; jobs/notebooks: set on the cluster
or just edit the defaults here)."""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


# --- Unity Catalog locations -------------------------------------------------
# Free Edition gives you a `workspace` catalog; a dedicated schema keeps it tidy.
CATALOG = _env("TRIAGE_CATALOG", "workspace")
SCHEMA = _env("TRIAGE_SCHEMA", "triage")


def fq(name: str) -> str:
    """Fully-qualified table name, e.g. fq('silver_reports')."""
    return f"{CATALOG}.{SCHEMA}.{name}"


VOLUME_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}"
LANDING_REPORTS = f"{VOLUME_ROOT}/landing/reports"   # Auto Loader source (JSON lines)
LANDING_ALERTS = f"{VOLUME_ROOT}/landing/alerts"
LANDING_GAUGES = f"{VOLUME_ROOT}/landing/gauges"
REFERENCE_DIR = f"{VOLUME_ROOT}/reference"           # manual downloads (CSV/TSV/PDF)
PHOTOS_DIR = f"{VOLUME_ROOT}/photos"                 # uploaded report photos
CODE_DIR = f"{VOLUME_ROOT}/code"                     # copy of src/ for the pipeline

# --- Model endpoints (check names in Playground / Serving) -------------------
# TODO(you): confirm these exist in your workspace (Serving -> Foundation models).
LLM_ENDPOINT = _env("TRIAGE_LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
VISION_ENDPOINT = _env("TRIAGE_VISION_ENDPOINT", "databricks-llama-4-maverick")
EMBEDDING_ENDPOINT = _env("TRIAGE_EMBEDDING_ENDPOINT", "databricks-gte-large-en")

# --- Vector Search -----------------------------------------------------------
VS_ENDPOINT = _env("TRIAGE_VS_ENDPOINT", "triage-vs")
VS_INDEX = fq("guidance_chunks_index")
USE_VECTOR_SEARCH = _env("TRIAGE_USE_VECTOR_SEARCH", "true").lower() == "true"

# --- Geospatial --------------------------------------------------------------
H3_RES_INCIDENT = 9   # ~0.1 km^2 cells: one "incident" = same cell + category
H3_RES_AREA = 7       # ~5 km^2 cells: forecasting unit, joins to county/SVI/gauges

# Default map center: Houston (Harvey back-test). Brazos County: 30.63, -96.33
DEFAULT_CENTER = (29.76, -95.37)

# --- External APIs -----------------------------------------------------------
# NWS asks for an identifying User-Agent. Use a project name + repo URL, not a
# personal email.
NWS_USER_AGENT = _env(
    "TRIAGE_NWS_USER_AGENT",
    "triage-datathon (github.com/domtz214/databricks-challenge)",
)
