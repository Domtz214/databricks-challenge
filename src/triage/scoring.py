"""Urgency score for an incident: one explainable formula used everywhere
(pipeline UDF, back-test, agent). Keep it transparent: every multiplier ends up
in `breakdown` so the app and the video can show *why* something is P1.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field


@dataclass
class IncidentSignals:
    p_life_threat: float                 # classifier probability rescue/medical
    in_flood_warning: bool = False       # active NWS flood/hurricane alert in county
    gauge_ft_above_flood: float | None = None  # nearest gauge stage - flood stage (ft)
    vulnerable: bool = False             # elderly/child/medical dependence mentioned
    svi_percentile: float | None = None  # CDC SVI overall percentile 0..1 (RPL_THEMES)
    minutes_waiting: float = 0.0         # since first report
    n_reports: int = 1                   # independent reports clustered into incident
    people_count: int | None = None
    forecast_risk: float | None = None   # rescue-need forecast for the area 0..1


@dataclass
class UrgencyResult:
    score: float
    tier: str
    breakdown: dict = field(default_factory=dict)


# TODO(you): tune weights + tier cutoffs on the Harvey eval set (notebook 08).
WEIGHTS = {
    "flood_warning": 0.5,
    "gauge_above": 0.5,
    "gauge_per_ft": 0.1,
    "gauge_per_ft_cap": 0.5,
    "vulnerable": 0.3,
    "svi": 0.2,
    "wait": 0.25,
    "corroboration": 0.15,
    "people": 0.05,
    "forecast": 0.3,
}
TIER_CUTOFFS = [("P1", 1.2), ("P2", 0.6), ("P3", 0.2)]  # else P4


def urgency(s: IncidentSignals, w: dict = WEIGHTS) -> UrgencyResult:
    base = max(0.0, min(1.0, s.p_life_threat))

    hazard = 1.0
    if s.in_flood_warning:
        hazard += w["flood_warning"]
    if s.gauge_ft_above_flood is not None and s.gauge_ft_above_flood > 0:
        hazard += w["gauge_above"] + min(
            w["gauge_per_ft_cap"], w["gauge_per_ft"] * s.gauge_ft_above_flood
        )

    vuln = 1.0 + (w["vulnerable"] if s.vulnerable else 0.0)
    if s.svi_percentile is not None:
        vuln += w["svi"] * max(0.0, min(1.0, s.svi_percentile))

    wait = 1.0 + w["wait"] * math.log1p(max(0.0, s.minutes_waiting) / 15.0)
    corroboration = 1.0 + w["corroboration"] * math.log2(max(1, s.n_reports))
    people = 1.0 + w["people"] * min(10, s.people_count or 1)
    forecast = 1.0 + w["forecast"] * (s.forecast_risk or 0.0)

    score = base * hazard * vuln * wait * corroboration * people * forecast

    # Hard rule (like a disqualifier, inverted): life threat + active flood +
    # vulnerable person is never below P1.
    hard_rule = base >= 0.5 and s.in_flood_warning and s.vulnerable
    if hard_rule:
        score = max(score, TIER_CUTOFFS[0][1])

    tier = next((t for t, cut in TIER_CUTOFFS if score >= cut), "P4")
    breakdown = {
        "base_p_life_threat": round(base, 3),
        "hazard_x": round(hazard, 3),
        "vulnerability_x": round(vuln, 3),
        "waiting_x": round(wait, 3),
        "corroboration_x": round(corroboration, 3),
        "people_x": round(people, 3),
        "forecast_x": round(forecast, 3),
        "hard_rule_p1": hard_rule,
        "signals": asdict(s),
    }
    return UrgencyResult(round(score, 4), tier, breakdown)
