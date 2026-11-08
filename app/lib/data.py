"""Cached data access for the app (SQL warehouse via the app's service principal)."""

from __future__ import annotations

import io
import json
import os
import uuid
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from triage import config
from triage.config import fq
from triage.db import WarehouseSql

TIER_COLORS = {"P1": [220, 38, 38, 200], "P2": [234, 88, 12, 180], "P3": [234, 179, 8, 160], "P4": [148, 163, 184, 120]}


@st.cache_resource
def db() -> WarehouseSql:
    return WarehouseSql()


@st.cache_resource
def agent():
    from triage.agent import TriageAgent

    return TriageAgent(db())


def q(sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.DataFrame(db().query(sql, params))


@st.cache_data(ttl=10)
def priority_queue(status: str = "open") -> pd.DataFrame:
    return q(f"""SELECT incident_id, category, tier, urgency, h3_cell, lat, lon, n_reports, people_count,
                        vulnerable, sample_texts, first_reported_at, county_fips, in_flood_warning,
                        gauge_ft_above_flood, forecast_risk, breakdown_json, langs
                 FROM {fq('gold_priority_queue')} WHERE status = :s ORDER BY urgency DESC LIMIT 500""",
             {"s": status})


@st.cache_data(ttl=60)
def facilities(kinds: tuple[str, ...] = ("shelter", "hospital", "nursing_home")) -> pd.DataFrame:
    in_list = ", ".join(f"'{k}'" for k in kinds)
    return q(f"SELECT name, kind, lat, lon, capacity FROM {fq('ref_facilities')} WHERE kind IN ({in_list})")


@st.cache_data(ttl=60)
def latest_forecast() -> pd.DataFrame:
    return q(f"""SELECT h3_area, risk, forecast_for FROM (
                   SELECT *, row_number() OVER (PARTITION BY h3_area ORDER BY forecast_for DESC) rn
                   FROM {fq('forecast_rescue_need')}) WHERE rn = 1 AND risk > 0.05""")


def record_decision(incident_id: str, decision: str, resource: str | None, rec: dict, user: str) -> None:
    db().execute(
        f"""INSERT INTO {fq('dispatch_decisions')}
            VALUES (:id, :decision, :resource, NULL, :agent_json, :user, current_timestamp())""",
        {"id": incident_id, "decision": decision, "resource": resource,
         "agent_json": json.dumps(rec), "user": user},
    )
    priority_queue.clear()


def submit_report(report: dict) -> str:
    """Write one report as a JSON file into the landing volume (Auto Loader picks it up)."""
    from databricks.sdk import WorkspaceClient

    path = f"{config.LANDING_REPORTS}/app_{uuid.uuid4().hex[:12]}.json"
    WorkspaceClient().files.upload(path, io.BytesIO(json.dumps(report).encode()), overwrite=True)
    return path


def upload_photo(data: bytes, suffix: str = "jpg") -> str:
    from databricks.sdk import WorkspaceClient

    path = f"{config.PHOTOS_DIR}/{datetime.now(timezone.utc):%Y%m%d}/{uuid.uuid4().hex[:12]}.{suffix}"
    WorkspaceClient().files.upload(path, io.BytesIO(data), overwrite=True)
    return path


def trigger_pipeline() -> None:
    """Kick a pipeline update so a new report shows up quickly (no-op if one is running)."""
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    name = os.getenv("TRIAGE_PIPELINE_NAME", "triage-pipeline")
    for p in w.pipelines.list_pipelines():
        if name in (p.name or ""):
            try:
                w.pipelines.start_update(p.pipeline_id)
            except Exception:
                pass
            return
