"""Robust daily cost outliers from local, deduplicated responses."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from .models import Finding


def detect(
    turns: list[dict], start: date | None, end: date
) -> tuple[list[Finding], bool]:
    days: dict[date, float] = defaultdict(float)
    unpriced: set[date] = set()
    for row in turns:
        day = date.fromisoformat(row["day"])
        if day > end:
            continue
        days[day] += row["cost"] or 0
        if row["cost"] is None:
            unpriced.add(day)
    if not days:
        return [], False
    first = min(days)
    findings = []
    enough = False
    for day in sorted(days):
        if start and day < start:
            continue
        count = min(28, (day - first).days)
        if count < 14:
            continue
        baseline_days = [day - timedelta(days=i) for i in range(1, count + 1)]
        # A missing price is unknown spend, not a zero-cost day in the baseline.
        if day in unpriced or any(d in unpriced for d in baseline_days):
            continue
        enough = True
        baseline = [days.get(d, 0.0) for d in baseline_days]
        center = median(baseline)
        mad = median(abs(value - center) for value in baseline)
        threshold = center + 3 * mad
        if days[day] > threshold:
            findings.append(
                Finding(
                    "anomaly",
                    "daily_cost",
                    "warning",
                    day.isoformat(),
                    day.isoformat(),
                    "Unusually expensive day",
                    "Local cost exceeds the trailing calendar-day median plus 3 × MAD. "
                    "Dates without stored activity inside the observed span count as zero.",
                    metrics={
                        "cost_usd": days[day],
                        "median_usd": center,
                        "mad_usd": mad,
                        "threshold_usd": threshold,
                        "baseline_days": count,
                    },
                )
            )
    return findings, enough
