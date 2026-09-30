"""Bounded local activity history, independent of billable usage."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
MAX_RECORDS = 500_000


def sync(conn: sqlite3.Connection, paths: list[Path]) -> dict:
    """Discard all content fields before storing the two activity coordinates."""
    stats = {"bytes": 0, "records": 0, "inserted": 0, "truncated": False}
    from . import identity

    claimed = identity.foreign_records("activity", "ts, project")
    for path in dict.fromkeys(paths):
        try:
            with path.open("rb") as stream:
                while True:
                    remaining = MAX_BYTES - stats["bytes"]
                    if stats["records"] >= MAX_RECORDS or remaining <= 0:
                        stats["truncated"] = bool(stream.read(1))
                        break
                    line = stream.readline(remaining + 1)
                    if not line:
                        break
                    if len(line) > remaining:
                        stats["truncated"] = True
                        break
                    stats["bytes"] += len(line)
                    stats["records"] += 1
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            continue
                        stamp, project = raw.get("timestamp"), raw.get("project")
                        del raw
                        if (
                            isinstance(stamp, bool)
                            or not isinstance(stamp, (int, float))
                            or not math.isfinite(stamp)
                            or not isinstance(project, str)
                        ):
                            continue
                        when = datetime.fromtimestamp(stamp / 1000, UTC)
                        if not 1970 <= when.year <= 9998:
                            continue
                    except (
                        ValueError,
                        OverflowError,
                        OSError,
                        UnicodeError,
                        RecursionError,
                    ):
                        continue
                    if (when.isoformat(), project) in claimed:
                        continue
                    stats["inserted"] += conn.execute(
                        "INSERT OR IGNORE INTO activity(ts, project) VALUES (?, ?)",
                        (when.isoformat(), project),
                    ).rowcount
        except OSError:
            continue
        if stats["truncated"]:
            break
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('activity_sync', ?)", (json.dumps(stats),)
    )
    return stats


def calendar(conn: sqlite3.Connection, tz: tzinfo, now: datetime) -> dict:
    """Activity dates use the report zone, including its shifted day boundary."""
    days: dict[str, int] = {}
    for row in conn.execute("SELECT ts FROM activity"):
        day = datetime.fromisoformat(row[0]).astimezone(tz).date().isoformat()
        days[day] = days.get(day, 0) + 1
    ordered = sorted(days)
    longest = run = 0
    previous = None
    for day in ordered:
        current = datetime.fromisoformat(day).date()
        run = run + 1 if previous and current - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = current
    cursor = now.astimezone(tz).date()
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    stats = conn.execute("SELECT value FROM meta WHERE key='activity_sync'").fetchone()
    return {
        "days": days,
        "current_streak": streak,
        "longest_streak": longest,
        "truncated": json.loads(stats[0])["truncated"] if stats else False,
    }
