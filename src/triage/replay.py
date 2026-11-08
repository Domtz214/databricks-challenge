"""Helpers for replaying historical tweets as a live stream, plus a synthetic
bilingual report generator so the pipeline runs end-to-end on day one,
before any downloads."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

TWITTER_EPOCH_MS = 1288834974657


def tweet_id_to_datetime(tweet_id: int | str) -> datetime:
    """Tweet IDs are Snowflake IDs: the top bits encode creation time. HumAID
    ships IDs + text but no timestamps, so this recovers the arrival order."""
    ms = (int(tweet_id) >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


_TEMPLATES = [
    ("en", "rescue_request", "We are trapped on the roof at {addr}, water is rising fast. {n} people, {vuln}. Please send a boat"),
    ("en", "rescue_request", "HELP my {rel} is stuck in the attic near {addr} water is chest deep"),
    ("en", "medical_emergency", "My {rel} needs dialysis and we can't get out, {addr}. Roads flooded"),
    ("en", "shelter_need", "Evacuated from {addr} with {n} kids, nowhere to go. Which shelter is open?"),
    ("en", "supplies_need", "No power at {addr} for 2 days, need water and baby formula"),
    ("en", "infrastructure_damage", "Bridge on {addr} is washed out, do not drive through"),
    ("en", "info_or_other", "Volunteers with boats meeting at {addr}, bring life jackets"),
    ("es", "rescue_request", "Estamos atrapados en el techo en {addr}, el agua esta subiendo. Somos {n} personas, {vuln_es}. Necesitamos un bote"),
    ("es", "medical_emergency", "Mi {rel_es} necesita insulina y no podemos salir de {addr}. Ayuda por favor"),
    ("es", "shelter_need", "Nos evacuaron de {addr}, no tenemos donde quedarnos con {n} ninos"),
    ("es", "supplies_need", "Sin luz en {addr}, necesitamos agua y comida"),
]
_REL = ["grandma", "grandpa", "mother", "neighbor", "son"]
_REL_ES = ["abuela", "abuelo", "mama", "vecino", "hijo"]
_VULN = ["including a baby", "my grandma uses a wheelchair", "two elderly", "all adults"]
_VULN_ES = ["con un bebe", "mi abuela en silla de ruedas", "dos ancianos", "todos adultos"]


def synthetic_reports(
    n: int,
    center: tuple[float, float],
    places: list[str] | None = None,
    start: datetime | None = None,
    minutes_span: int = 180,
    seed: int | None = None,
) -> list[dict]:
    """Bilingual synthetic reports scattered ~15 km around `center`.
    Each row matches the landing schema the pipeline expects."""
    rng = random.Random(seed)
    start = start or datetime.now(timezone.utc)
    places = places or ["Braeswood Blvd", "Meyerland", "Kingwood Dr", "Telephone Rd", "Clear Lake"]
    rows = []
    for _ in range(n):
        lang, label, tpl = rng.choice(_TEMPLATES)
        text = tpl.format(
            addr=rng.choice(places),
            n=rng.randint(1, 7),
            vuln=rng.choice(_VULN),
            vuln_es=rng.choice(_VULN_ES),
            rel=rng.choice(_REL),
            rel_es=rng.choice(_REL_ES),
        )
        has_gps = rng.random() < 0.6  # rest must be geocoded from text
        rows.append({
            "report_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "source": "synthetic",
            "text": text,
            "lang": lang,
            "lat": center[0] + rng.gauss(0, 0.08) if has_gps else None,
            "lon": center[1] + rng.gauss(0, 0.08) if has_gps else None,
            "reported_at": (start + timedelta(minutes=rng.uniform(0, minutes_span))).isoformat(),
            "photo_path": None,
            "reporter_contact": None,
            "event": "synthetic",
            "ground_truth_label": label,
        })
    return rows
