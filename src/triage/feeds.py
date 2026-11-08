"""Live public feeds: NWS alerts and USGS stream gauges.

`parse_*` functions are pure (unit-tested with fixtures). `fetch_*` do HTTP.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import config

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
# Legacy USGS instantaneous-values service. USGS is migrating to
# api.waterdata.usgs.gov; TODO(you): if this endpoint is retired, switch to the
# OGC API (parameter_code=00065) and update parse_usgs_iv.
USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"

FLOOD_EVENT_PATTERN = r"(?i)flood|hurricane|tropical storm|storm surge"


def parse_nws_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for feat in payload.get("features", []):
        p = feat.get("properties", {}) or {}
        geocode = p.get("geocode", {}) or {}
        same = geocode.get("SAME", []) or []
        rows.append({
            "alert_id": p.get("id") or feat.get("id"),
            "event": p.get("event"),
            "severity": p.get("severity"),
            "urgency": p.get("urgency"),
            "certainty": p.get("certainty"),
            "headline": p.get("headline"),
            "area_desc": p.get("areaDesc"),
            # SAME '048201' -> county FIPS '48201'
            "county_fips": [c[1:] for c in same if len(c) == 6],
            "ugc_codes": geocode.get("UGC", []) or [],
            "sent": p.get("sent"),
            "effective": p.get("effective"),
            "expires": p.get("expires"),
            "geometry_json": None if feat.get("geometry") is None else json.dumps(feat["geometry"]),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def parse_usgs_iv(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for ts in payload.get("value", {}).get("timeSeries", []):
        info = ts.get("sourceInfo", {})
        loc = info.get("geoLocation", {}).get("geogLocation", {})
        site = (info.get("siteCode") or [{}])[0].get("value")
        var = ts.get("variable", {})
        no_data = var.get("noDataValue")
        param = (var.get("variableCode") or [{}])[0].get("value")
        for block in ts.get("values", []):
            for v in block.get("value", []):
                try:
                    val = float(v["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                if no_data is not None and val == float(no_data):
                    continue
                rows.append({
                    "site_id": site,
                    "site_name": info.get("siteName"),
                    "lat": loc.get("latitude"),
                    "lon": loc.get("longitude"),
                    "parameter_code": param,
                    "value": val,
                    "observed_at": v.get("dateTime"),
                })
    return rows


def fetch_nws_alerts(area: str = "TX", timeout: int = 30) -> list[dict[str, Any]]:
    import requests

    r = requests.get(
        NWS_ALERTS_URL,
        params={"area": area},
        headers={"User-Agent": config.NWS_USER_AGENT, "Accept": "application/geo+json"},
        timeout=timeout,
    )
    r.raise_for_status()
    return parse_nws_alerts(r.json())


def fetch_usgs_gauges(
    state: str = "tx",
    parameter_code: str = "00065",   # gage height, ft
    period: str | None = "PT3H",     # last 3h, for rise rate
    start: str | None = None,        # ISO, for historical (Harvey: 2017-08-25)
    end: str | None = None,
    sites: list[str] | None = None,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    import requests

    params: dict[str, Any] = {"format": "json", "parameterCd": parameter_code, "siteStatus": "all"}
    if sites:
        params["sites"] = ",".join(sites)
    else:
        params["stateCd"] = state
    if start:
        params["startDT"], params["endDT"] = start, end
    elif period:
        params["period"] = period
    r = requests.get(USGS_IV_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return parse_usgs_iv(r.json())
