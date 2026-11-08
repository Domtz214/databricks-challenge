"""Harvey back-test: how much sooner do life-threatening reports get handled
when coordinators work a Triage-ranked queue instead of first-come-first-served?

Model: a dispatch desk with fixed capacity (reports handled per hour). Every
report waits in a queue until the desk picks it. We compare queue policies and
measure wait time for life-threatening reports. This is the headline number.

Important: priority for a report must only use information available at its
arrival time (no leakage). Notebook 08 computes `priority` that way.
"""

from __future__ import annotations

import heapq
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class QueuedReport:
    report_id: str
    arrived_at: datetime
    priority: float          # higher = more urgent
    life_threatening: bool   # ground truth label (from HumAID / your relabels)


def simulate(
    reports: list[QueuedReport],
    capacity_per_hour: float,
    policy: str = "priority",
) -> dict[str, float]:
    """Return {report_id: wait_minutes}. policy in {'fifo', 'priority'}."""
    if capacity_per_hour <= 0:
        raise ValueError("capacity_per_hour must be > 0")
    if policy not in {"fifo", "priority"}:
        raise ValueError(f"unknown policy {policy!r}")

    arrivals = sorted(reports, key=lambda r: r.arrived_at)
    service = timedelta(hours=1 / capacity_per_hour)
    waits: dict[str, float] = {}
    heap: list[tuple] = []
    i, n = 0, len(arrivals)
    t = arrivals[0].arrived_at if arrivals else None
    seq = 0

    while i < n or heap:
        while i < n and arrivals[i].arrived_at <= t:
            r = arrivals[i]
            key = (r.arrived_at,) if policy == "fifo" else (-r.priority, r.arrived_at)
            heapq.heappush(heap, (*key, seq, r))
            seq += 1
            i += 1
        if not heap:
            t = arrivals[i].arrived_at
            continue
        r = heapq.heappop(heap)[-1]
        waits[r.report_id] = (t - r.arrived_at).total_seconds() / 60.0
        t = t + service
    return waits


def summarize(reports: list[QueuedReport], waits: dict[str, float]) -> dict[str, float]:
    life = [waits[r.report_id] for r in reports if r.life_threatening and r.report_id in waits]
    other = [waits[r.report_id] for r in reports if not r.life_threatening and r.report_id in waits]

    def pct(xs: list[float], q: float) -> float:
        if not xs:
            return float("nan")
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    return {
        "n_life_threatening": len(life),
        "life_median_wait_min": statistics.median(life) if life else float("nan"),
        "life_p90_wait_min": pct(life, 0.9),
        "other_median_wait_min": statistics.median(other) if other else float("nan"),
    }


def compare_policies(reports: list[QueuedReport], capacity_per_hour: float) -> dict:
    fifo = summarize(reports, simulate(reports, capacity_per_hour, "fifo"))
    prio = summarize(reports, simulate(reports, capacity_per_hour, "priority"))
    speedup = fifo["life_median_wait_min"] - prio["life_median_wait_min"]
    return {"fifo": fifo, "triage": prio, "life_median_minutes_saved": speedup}


def lead_time_hours(first_flagged_at: datetime | None, first_report_at: datetime | None) -> float | None:
    """Forecast lead time for an area/facility: positive = flagged before the
    first rescue report arrived."""
    if first_flagged_at is None or first_report_at is None:
        return None
    return (first_report_at - first_flagged_at).total_seconds() / 3600.0
