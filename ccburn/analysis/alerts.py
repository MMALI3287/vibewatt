"""Durable quota forecasts and local response-cost alerts without network IO."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone, tzinfo
from statistics import median

from .. import store

_SCOPE = "phase6-alerts"


def _time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value or "")
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def _alert(kind: str, key: str, title: str, detail: str, now: datetime) -> dict:
    return {
        "id": hashlib.sha256(f"{_SCOPE}:{kind}:{key}".encode()).hexdigest(),
        "kind": kind,
        "severity": "warning",
        "day": now.date().isoformat(),
        "subject": key,
        "title": title,
        "detail": detail,
    }


def evaluate(
    conn: sqlite3.Connection,
    tz: tzinfo,
    *,
    now: datetime | None = None,
    overrides: dict | None = None,
    session_hours: int = 5,
) -> dict:
    """Return active alerts and the number first observed in this transaction.

    Costs are the stored sync-time prices. Quota percentages are never inferred
    from local dollars or tokens. Findings preserve fired identities indefinitely.
    """
    instant = (now or datetime.now(tz)).astimezone(timezone.utc)
    candidates = []
    labels: dict[str, list[tuple[datetime, dict]]] = {}
    for row in conn.execute("SELECT * FROM quota_samples"):
        sample = dict(row)
        stamp = _time(sample["ts"])
        if stamp and stamp <= instant:
            labels.setdefault(sample["label"], []).append((stamp, sample))
    for label, samples in labels.items():
        samples.sort(key=lambda pair: pair[0])
        if len(samples) < 2:
            continue
        latest_time, latest = samples[-1]
        previous_time, previous = samples[-2]
        reset = _time(latest["resets_at"])
        if (
            not reset
            or reset <= instant
            or reset != _time(previous["resets_at"])
            or latest_time <= previous_time
            or not _number(latest["utilization"])
            or not _number(previous["utilization"])
            or latest["utilization"] > 100
            or previous["utilization"] > 100
        ):
            continue
        slope = (latest["utilization"] - previous["utilization"]) / (
            latest_time - previous_time
        ).total_seconds()
        projected = (
            latest["utilization"] + slope * (reset - latest_time).total_seconds()
        )
        if slope > 0 and projected > 100:
            candidates.append(
                _alert(
                    "burn",
                    f"{label}:{reset.isoformat()}",
                    f"{label} may reach its limit",
                    f"Account-wide utilization projects to {projected:.1f}% at reset "
                    f"({reset.isoformat()}) from the last two quota samples.",
                    instant,
                )
            )
    rows = []
    for row in conn.execute("SELECT * FROM turns"):
        item = dict(row)
        stamp = _time(item["ts"])
        if stamp and stamp <= instant:
            rows.append((stamp, item))
    rows.sort(key=lambda pair: (pair[0], pair[1]["msg_id"], pair[1]["request_id"]))
    baseline: deque[float] = deque(maxlen=50)
    active = None
    block_start = None
    block_end = None
    tokens = 0
    cost = 0.0
    for stamp, row in rows:
        if _number(row["cost"]):
            if len(baseline) == 50 and row["cost"] > 5 * median(baseline):
                candidates.append(
                    _alert(
                        "spike",
                        repr((row["msg_id"], row["request_id"])),
                        "Unusually expensive local response",
                        f"Local response cost ${row['cost']:.4f} exceeds 5x the previous "
                        f"50 priced responses' median (${median(baseline):.4f}).",
                        instant,
                    )
                )
            baseline.append(row["cost"])
        if block_end is None or stamp >= block_end:
            block_start = stamp.replace(minute=0, second=0, microsecond=0)
            block_end = block_start + timedelta(hours=max(1, session_hours))
            tokens, cost = 0, 0.0
        tokens += sum(
            row[k] for k in ("input", "cache_5m", "cache_1h", "cache_read", "output")
        )
        if _number(row["cost"]):
            cost += row["cost"]
    if block_start is not None and instant < block_end:
        minutes = max(1.0, (instant - block_start).total_seconds() / 60)
        active = {
            "tokens_per_minute": tokens / minutes,
            "cost_per_minute": cost / minutes,
        }
    with conn:
        # Serialize identity checks so concurrent dashboard requests cannot both fire.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        known = {
            r[0]
            for r in conn.execute("SELECT id FROM findings WHERE scope = ?", (_SCOPE,))
        }
        new_count = sum(item["id"] not in known for item in candidates)
        saved = store.save_findings(conn, _SCOPE, candidates)
    return {
        "alerts": [
            {key: item[key] for key in ("id", "kind", "title", "detail", "created_at")}
            for item in saved
            if not item["dismissed"]
        ],
        "new_count": new_count,
        "active_block": active,
        "notes": [
            (
                "Quota forecasts are account-wide. Response spikes and block burn rates are local-only. "
                "Block cost uses priced stored responses only; unknown model costs are excluded."
            )
        ],
    }
