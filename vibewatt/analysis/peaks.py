"""Repeated limit encounters, not repeated polling of the same window."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, tzinfo

from ..config import clock_zone
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
        clock = timestamp(row["ts"]).astimezone(clock_zone(tz))
        groups[(row["label"], clock.weekday(), clock.hour)].append(row)
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
        # Name the zone, not just "the report timezone" (A-122).
        shown = clock_zone(tz)
        zone = getattr(shown, "key", None) or datetime.now(shown).strftime("UTC%z")
        findings.append(
            Finding(
                "peak",
                "peak_window",
                "info",
                f"{label}:{weekday}:{hour}",
                max(dates).isoformat(),
                f"Limits encountered on {name} around {hour:02d}:00 ({zone})",
                "Account-wide samples reached 100% in at least two distinct reset windows on "
                f"different dates at this weekday/hour in {zone}. Repeated polling "
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
