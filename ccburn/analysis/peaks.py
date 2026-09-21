"""Repeated limit encounters, not repeated polling of the same window."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, tzinfo

from .models import Finding, timestamp


def detect(
    samples: list[dict], tz: tzinfo, start: date | None = None, end: date | None = None
) -> list[Finding]:
    encounters: dict[tuple[str, str], dict] = {}
    for row in sorted(samples, key=lambda r: r["ts"]):
        if row["utilization"] < 100 or not row["resets_at"]:
            continue
        reset = timestamp(row["resets_at"])
        stamp = timestamp(row["ts"])
        if reset is None or stamp is None or stamp >= reset:
            continue
        encounters.setdefault((row["label"], reset.isoformat()), row)
    groups: dict[tuple[str, int, int], list[dict]] = defaultdict(list)
    for row in encounters.values():
        stamp = timestamp(row["ts"]).astimezone(tz)
        if (start and stamp.date() < start) or (end and stamp.date() > end):
            continue
        groups[(row["label"], stamp.weekday(), stamp.hour)].append(row)
    findings = []
    for (label, weekday, hour), rows in sorted(groups.items()):
        dates = {timestamp(r["ts"]).astimezone(tz).date() for r in rows}
        if len(dates) < 2:
            continue
        name = (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )[weekday]
        findings.append(
            Finding(
                "peak",
                "peak_window",
                "info",
                f"{label}:{weekday}:{hour}",
                max(dates).isoformat(),
                f"Limits encountered on {name} around {hour:02d}:00",
                "Account-wide samples reached 100% in at least two distinct reset windows on "
                "different dates at this weekday/hour in the report timezone. Repeated polling "
                "counts once per reset window. This describes observed history, not a prediction.",
                coverage="account",
                metrics={
                    "window": label,
                    "weekday": weekday,
                    "hour": hour,
                    "encounters": len(rows),
                },
            )
        )
    return findings
