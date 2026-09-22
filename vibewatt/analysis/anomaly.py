"""Robust daily cost outliers from local, deduplicated responses.

A day is compared with this user's previous active days only: an inactive day
is not a $0 day. Zero-filling calendar gaps flagged every working day for
someone active three days a week as well as every day back from a break (A-041).

The threshold is `median + 3 × max(1.4826 × MAD, 0.1 × median)`. 1.4826 scales
MAD to a standard deviation for normal data, which cut false positives on
stable spend from 6-10% to 3-6% (A-120). The floor of 10% of the median stops
a perfectly flat baseline (MAD = 0) from flagging any day above it, including
float summation noise (A-042).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import median

from .models import Finding

MIN_ACTIVE_DAYS = 14
MAX_ACTIVE_DAYS = 28
MAD_SCALE = 1.4826
FLOOR = 0.1


def threshold(baseline: list[float]) -> tuple[float, float, float]:
    """(threshold, median, scaled spread) for a baseline of daily costs."""
    center = median(baseline)
    mad = median(abs(value - center) for value in baseline)
    spread = max(MAD_SCALE * mad, FLOOR * center)
    return center + 3 * spread, center, spread


def detect(turns: list[dict], start: date | None, end: date) -> tuple[list[Finding], dict]:
    """Findings, plus why detection could not run where it could not.

    The status says how many days were evaluated and, for the rest, whether
    history was too short or the day itself had unpriced spend (naming the
    models), so the UI never shows "no anomalies" when it could not look.
    """
    cost: dict[date, float] = defaultdict(float)
    unpriced: dict[date, set[str]] = defaultdict(set)
    for row in turns:
        day = date.fromisoformat(row["day"])
        if day > end:
            continue
        if row["cost"] is None:
            unpriced[day].add(row["model"])
        else:
            cost[day] += row["cost"]
    active = sorted(set(cost) | set(unpriced))
    in_range = [d for d in active if not start or d >= start]
    status = {"evaluated": 0, "short_history": 0, "unpriced": 0, "unpriced_models": [],
              "active_days": len(in_range)}
    findings: list[Finding] = []
    models: set[str] = set()
    for day in in_range:
        if unpriced.get(day):
            status["unpriced"] += 1
            models |= unpriced[day]
            continue
        # Earlier fully priced active days. A day with unpriced spend is left
        # out of the baseline rather than blocking every day after it (A-043).
        prior = [d for d in active if d < day and not unpriced.get(d)][-MAX_ACTIVE_DAYS:]
        if len(prior) < MIN_ACTIVE_DAYS:
            status["short_history"] += 1
            continue
        status["evaluated"] += 1
        limit, center, spread = threshold([cost[d] for d in prior])
        if cost[day] > limit:
            findings.append(Finding(
                "anomaly", "daily_cost", "warning", day.isoformat(), day.isoformat(),
                "Unusually expensive day",
                "Local cost exceeds the median of your previous active days by more "
                "than 3 robust standard deviations. Days without activity are not "
                "counted as $0.",
                metrics={
                    "cost_usd": cost[day],
                    "median_usd": center,
                    "spread_usd": spread,
                    "threshold_usd": limit,
                    "baseline_days": len(prior),
                },
            ))
    status["unpriced_models"] = sorted(models)
    return findings, status


def notes(status: dict) -> list[str]:
    """Plain statements of what anomaly detection could and could not check."""
    out = []
    if status["active_days"] == 0:
        out.append("Anomaly detection: no local activity in this range.")
        return out
    if status["short_history"]:
        out.append(
            f"Anomaly detection skipped {status['short_history']} day(s) with fewer than "
            f"{MIN_ACTIVE_DAYS} earlier active days to compare against (not enough history).")
    if status["unpriced"]:
        out.append(
            f"Anomaly detection skipped {status['unpriced']} day(s) with unpriced spend "
            f"from {', '.join(status['unpriced_models'])}.")
    return out
