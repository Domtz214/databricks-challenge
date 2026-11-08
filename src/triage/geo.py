"""Lightweight geo helpers (no Spark). H3 indexing itself happens in SQL with
Databricks' built-in h3_* functions; this module covers distance, nearest
lookups and turning free-text locations into coordinates."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .taxonomy import normalize

EARTH_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


# Same formula as SQL, for use in spark.sql / warehouse queries.
def haversine_sql(lat1: str, lon1: str, lat2: str, lon2: str) -> str:
    return (
        f"2 * {EARTH_KM} * asin(sqrt(pow(sin(radians({lat2} - {lat1}) / 2), 2) + "
        f"cos(radians({lat1})) * cos(radians({lat2})) * "
        f"pow(sin(radians({lon2} - {lon1}) / 2), 2)))"
    )


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    kind: str = "place"   # neighborhood | street | landmark | zip | city


class Gazetteer:
    """Longest-match lookup of known place names inside free text.

    Feed it Houston neighborhoods/landmarks/ZIPs (notebook 01 builds
    `ref_gazetteer`). Good enough for tweets like "trapped in Meyerland near
    Braeswood" where a street geocoder fails.
    """

    def __init__(self, places: list[Place]):
        self._places = sorted(
            ((normalize(p.name), p) for p in places if p.name),
            key=lambda x: -len(x[0]),
        )

    def match(self, text: str) -> Place | None:
        t = f" {normalize(text)} "
        for key, place in self._places:
            if f" {key} " in t or (key.isdigit() and key in t):
                return place
        return None


def geocode_census(address: str, timeout: int = 15) -> tuple[float, float] | None:
    """US Census one-line geocoder (free, no key). Returns (lat, lon)."""
    import requests

    r = requests.get(
        "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
        params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
        timeout=timeout,
    )
    r.raise_for_status()
    matches = r.json().get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    c = matches[0]["coordinates"]
    return float(c["y"]), float(c["x"])
