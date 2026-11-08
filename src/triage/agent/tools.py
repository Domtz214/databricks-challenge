"""Agent tools: plain functions over the gold tables + an OpenAI-style schema
list. They take a `Sql` backend so they run in notebooks and in the App."""

from __future__ import annotations

import json
import math
from typing import Any, Callable

from .. import config
from ..db import Sql
from ..geo import haversine_sql


def get_incident(db: Sql, incident_id: str) -> dict[str, Any]:
    rows = db.query(
        f"""SELECT incident_id, category, tier, urgency, lat, lon, county_fips, n_reports,
                   people_count, vulnerable, sample_texts, first_reported_at, breakdown_json
            FROM {config.fq('gold_priority_queue')} WHERE incident_id = :id""",
        {"id": incident_id},
    )
    return rows[0] if rows else {"error": f"incident {incident_id} not found"}


def top_incidents(db: Sql, n: int = 10) -> list[dict[str, Any]]:
    return db.query(
        f"""SELECT incident_id, category, tier, urgency, n_reports, people_count, vulnerable
            FROM {config.fq('gold_priority_queue')}
            WHERE status = 'open' ORDER BY urgency DESC LIMIT {int(n)}"""
    )


def nearest_facilities(db: Sql, lat: float, lon: float, kind: str = "shelter", n: int = 3) -> list[dict[str, Any]]:
    dist = haversine_sql(":lat", ":lon", "lat", "lon")
    return db.query(
        f"""SELECT name, kind, address, capacity, round({dist}, 2) AS distance_km
            FROM {config.fq('ref_facilities')}
            WHERE kind = :kind AND coalesce(is_open, true)
            ORDER BY distance_km LIMIT {int(n)}""",
        {"lat": lat, "lon": lon, "kind": kind},
    )
    # TODO(you) stretch: drop facilities whose access cell is forecast flooded
    # (join forecast_rescue_need on h3_area) -> "route not flooded" claim.


def flood_context(db: Sql, lat: float, lon: float, county_fips: str | None = None) -> dict[str, Any]:
    alerts = []
    if county_fips:
        alerts = db.query(
            f"""SELECT event, severity, headline, expires
                FROM {config.fq('silver_alerts_active')} WHERE county_fips = :c""",
            {"c": county_fips},
        )
    dist = haversine_sql(":lat", ":lon", "lat", "lon")
    gauges = db.query(
        f"""SELECT site_name, stage_ft, flood_stage_ft, ft_above_flood, rise_ft_3h,
                   round({dist}, 1) AS distance_km
            FROM {config.fq('silver_gauges_latest')} ORDER BY distance_km LIMIT 1""",
        {"lat": lat, "lon": lon},
    )
    return {"active_alerts": alerts, "nearest_gauge": gauges[0] if gauges else None}


def search_guidance(db: Sql, query: str, k: int = 4) -> list[dict[str, Any]]:
    if config.USE_VECTOR_SEARCH:
        try:
            from databricks.vector_search.client import VectorSearchClient

            idx = VectorSearchClient(disable_notice=True).get_index(config.VS_ENDPOINT, config.VS_INDEX)
            res = idx.similarity_search(query_text=query, columns=["source", "page", "text"], num_results=k)
            cols = [c["name"] for c in res["manifest"]["columns"]]
            return [dict(zip(cols, row)) for row in res["result"]["data_array"]]
        except Exception as e:  # fall through to brute force
            print(f"[search_guidance] vector search unavailable ({e}); using SQL fallback")
    return _search_guidance_bruteforce(db, query, k)


def _search_guidance_bruteforce(db: Sql, query: str, k: int) -> list[dict[str, Any]]:
    """Fallback without Vector Search: embed the query with ai_query and do
    cosine similarity in SQL over the precomputed embedding column."""
    rows = db.query(
        f"""WITH q AS (SELECT ai_query('{config.EMBEDDING_ENDPOINT}', :q) AS qe)
            SELECT source, page, text,
                   aggregate(zip_with(embedding, q.qe, (a, b) -> a * b), 0D, (acc, x) -> acc + x)
                     / (sqrt(aggregate(embedding, 0D, (acc, x) -> acc + x * x))
                        * sqrt(aggregate(q.qe, 0D, (acc, x) -> acc + x * x))) AS score
            FROM {config.fq('guidance_chunks')}, q
            ORDER BY score DESC LIMIT {int(k)}""",
        {"q": query},
    )
    return rows


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_incident", "description": "Read an incident: reports, location, people, vulnerability, urgency breakdown.",
        "parameters": {"type": "object", "properties": {"incident_id": {"type": "string"}}, "required": ["incident_id"]}}},
    {"type": "function", "function": {
        "name": "flood_context", "description": "Active NWS alerts for the county and the nearest USGS stream gauge vs flood stage.",
        "parameters": {"type": "object", "properties": {
            "lat": {"type": "number"}, "lon": {"type": "number"}, "county_fips": {"type": "string"}},
            "required": ["lat", "lon"]}}},
    {"type": "function", "function": {
        "name": "nearest_facilities", "description": "Nearest open facilities of a kind.",
        "parameters": {"type": "object", "properties": {
            "lat": {"type": "number"}, "lon": {"type": "number"},
            "kind": {"type": "string", "enum": ["shelter", "hospital", "nursing_home", "dialysis", "fire_station"]},
            "n": {"type": "integer"}}, "required": ["lat", "lon", "kind"]}}},
    {"type": "function", "function": {
        "name": "search_guidance", "description": "Search Red Cross / FEMA / NWS response guidance documents.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]


def make_tool_registry(db: Sql) -> dict[str, Callable[..., Any]]:
    return {
        "get_incident": lambda **a: get_incident(db, **a),
        "flood_context": lambda **a: flood_context(db, **a),
        "nearest_facilities": lambda **a: nearest_facilities(db, **a),
        "search_guidance": lambda **a: search_guidance(db, **a),
    }


def to_json(obj: Any) -> str:
    def default(o):
        if isinstance(o, float) and math.isnan(o):
            return None
        return str(o)

    return json.dumps(obj, default=default)[:8000]
