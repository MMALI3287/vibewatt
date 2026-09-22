"""Durable rollups that outlive log pruning.

Claude Code deletes session logs after ``cleanupPeriodDays`` (30 by default),
so a tool that only reads live logs quietly loses your older history. vibewatt
writes a small per-day, per-model rollup to its own data directory and merges
it back on every run.

Live logs are authoritative for any day they still cover; stored rows only fill
in days the logs no longer reach. Each stored day also keeps the cost that was
computed when the day was fresh, so a later price change does not silently
rewrite what last quarter cost.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

from .config import data_dir

SCHEMA = 1
FIELDS = (
    "responses", "input", "cache_5m", "cache_1h",
    "cache_read", "output", "thinking", "web_searches", "cost",
)


def path() -> Path:
    return data_dir() / "history.json"


def load() -> dict:
    try:
        with path().open("r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"schema": SCHEMA, "days": {}}
    if not isinstance(blob, dict) or blob.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "days": {}}
    if not isinstance(blob.get("days"), dict):
        blob["days"] = {}
    return blob


def _atomic_write(target: Path, blob: dict) -> None:
    """Write via a temp file in the same directory so a crash cannot truncate."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, separators=(",", ":"))
        os.replace(tmp, target)          # atomic on POSIX and Windows alike
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def merge(report) -> dict:
    """Fold the live report into stored history and persist. Returns the store."""
    blob = load()
    days = blob["days"]
    for (day, model), bucket in report.by_day_model.items():
        key = f"{day}|{model}"
        days[key] = {
            "responses": bucket.turns,
            "input": bucket.input,
            "cache_5m": bucket.cache_5m,
            "cache_1h": bucket.cache_1h,
            "cache_read": bucket.cache_read,
            "output": bucket.output,
            "thinking": bucket.thinking,
            "web_searches": bucket.web_searches,
            "cost": round(bucket.cost, 6),
            "source": next(iter(report.by_source), "claude-code"),
        }
    _atomic_write(path(), blob)
    return blob


def restore(report, blob: dict | None = None) -> int:
    """Add stored days the live logs no longer cover. Returns how many were added."""
    from .aggregate import Bucket

    blob = blob if blob is not None else load()
    live_days = set(report.by_day)
    added = 0
    for key, row in blob.get("days", {}).items():
        try:
            day_str, model = key.split("|", 1)
            day = date.fromisoformat(day_str)
        except ValueError:
            continue
        if day in live_days:
            continue                      # live logs win for days they still cover
        bucket = Bucket()
        bucket.turns = int(row.get("responses", 0))
        for field in FIELDS[1:-1]:
            setattr(bucket, field, int(row.get(field, 0)))
        bucket.cost = float(row.get("cost", 0.0))
        report.by_day[day] = _combine(report.by_day.get(day), bucket)
        report.by_day_model[(day, model)] = bucket
        report.by_model[model] = _combine(report.by_model.get(model), bucket)
        report.total = _combine(report.total, bucket)
        report.restored_days.add(day)
        added += 1
    return added


def _combine(existing, bucket):
    from .aggregate import Bucket

    if existing is None:
        out = Bucket()
    else:
        out = existing
    out.turns += bucket.turns
    out.input += bucket.input
    out.cache_5m += bucket.cache_5m
    out.cache_1h += bucket.cache_1h
    out.cache_read += bucket.cache_read
    out.output += bucket.output
    out.thinking += bucket.thinking
    out.web_searches += bucket.web_searches
    out.cost += bucket.cost
    return out
