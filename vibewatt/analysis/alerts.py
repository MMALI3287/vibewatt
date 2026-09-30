"""Durable quota forecasts and local response-cost alerts without network IO."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from collections import deque
from datetime import UTC, datetime, timedelta, tzinfo
from statistics import median

from .. import store

_SCOPE = "phase6-alerts"


def _time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value or "")
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


SPIKE_LOOKBACK = timedelta(hours=24)


def _alert(kind: str, key: str, title: str, detail: str, day: str) -> dict:
    return {
        "id": hashlib.sha256(f"{_SCOPE}:{kind}:{key}".encode()).hexdigest(),
        "kind": kind,
        "severity": "warning",
        "day": day,
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

    A quota alert belongs to one window episode, so it fires once however many
    samples arrive (A-006, A-048). It fires when a window reaches 100 %, or when
    this account's own history puts the median projection at reset at 100 % or
    more. A spike alert covers the last 24 hours only (A-050). Alert days are
    in the report timezone (A-096).
    """
    from ..aggregate import from_store
    from .forecast import forecast

    instant = (now or datetime.now(tz)).astimezone(UTC)
    day = instant.astimezone(tz).date().isoformat()
    candidates = []
    for window in forecast(conn, instant):
        key = f"{window['key']}:{window['scope']}:{window['episode_start']}"
        band = window["band"]
        if window["used"] >= 100:
            detail = (
                f"Account-wide utilization reached {window['used']:.0f}% before the "
                f"reset at {window['resets_at']}."
            )
        elif band and band["p50"] >= 100:
            detail = (
                f"At {window['used']:.0f}% with {window['elapsed_pct']:.0f}% of the window "
                f"gone, this account's past windows put the reset at "
                f"{band['p10']:.0f}-{band['p90']:.0f}% (median {band['p50']:.0f}%)."
            )
        else:
            continue
        candidates.append(
            _alert("burn", key, f"{window['label']} may reach its limit", detail, day)
        )

    # Spikes: responses in the last 24 hours against the 50 priced responses
    # before each one. Older spikes are history, not active alerts.
    since = (instant - SPIKE_LOOKBACK).isoformat()
    prior = [
        r[0]
        for r in conn.execute(
            "SELECT cost FROM turns WHERE ts < ? AND cost IS NOT NULL AND cost >= 0"
            " ORDER BY ts DESC LIMIT 50",
            (since,),
        )
    ][::-1]
    baseline: deque[float] = deque(prior, maxlen=50)
    for row in conn.execute(
        "SELECT msg_id, request_id, ts, cost FROM turns WHERE ts >= ? AND ts <= ?"
        " ORDER BY ts, msg_id, request_id",
        (since, instant.isoformat()),
    ):
        if not _number(row["cost"]):
            continue
        if len(baseline) == 50 and row["cost"] > 5 * median(baseline):
            candidates.append(
                _alert(
                    "spike",
                    repr((row["msg_id"], row["request_id"])),
                    "Unusually expensive local response",
                    f"Local response cost ${row['cost']:.4f} exceeds 5x the previous "
                    f"50 priced responses' median (${median(baseline):.4f}).",
                    day,
                )
            )
        baseline.append(row["cost"])

    active = None
    report = from_store(
        conn,
        tz,
        session_hours=session_hours,
        overrides=overrides,
        parts=frozenset({"blocks"}),
    )
    for block in reversed(report.blocks):
        if block.start <= instant < block.end:
            minutes = max(1.0, (instant - block.start).total_seconds() / 60)
            active = {
                "tokens_per_minute": block.bucket.total_tokens / minutes,
                "cost_per_minute": block.bucket.cost / minutes,
            }
            break
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
            {
                key: item[key]
                for key in ("id", "kind", "severity", "title", "detail", "created_at")
            }
            for item in saved
            if not item["dismissed"]
        ],
        "new_count": new_count,
        "active_block": active,
        "notes": [
            (
                "Quota alerts are account-wide. Response spikes and block burn rates are local-only. "
                "Block cost uses priced stored responses only; unknown model costs are excluded."
            )
        ],
    }
