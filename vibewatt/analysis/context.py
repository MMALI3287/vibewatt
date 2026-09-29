"""Harvested context snapshots and evidence-bounded local context nudges."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from ..pricing import normalize
from .models import Finding


def detect(sessions: list[dict]) -> list[Finding]:
    findings = []
    for row in sessions:
        used, maximum = row["context_used"], row["context_max"]
        if used is None or maximum is None or used < 0 or maximum <= 0:
            continue
        ratio = used / maximum
        if ratio <= 0.7:
            continue
        findings.append(
            Finding(
                "context",
                "context_window",
                "urgent" if ratio > 0.85 else "warning",
                row["id"],
                row["day"],
                "Context window nearing capacity",
                "Latest harvested snapshot, not a live measurement or a historical peak. "
                "Consider a concise handoff before the context window fills.",
                coverage="harvested",
                session_id=row["id"],
                metrics={
                    "used_tokens": used,
                    "max_tokens": maximum,
                    "utilization": ratio,
                    "snapshot_at": row["ended"],
                },
            )
        )
    return findings


# Retrieved 2026-09-29: https://platform.claude.com/docs/en/build-with-claude/context-windows
# Claude Code can use a smaller window than the API maximum:
# https://code.claude.com/docs/en/model-config#extended-context
# The Phase 8 contract requires session evidence before assuming 1M.
STANDARD_MODELS = {
    "claude-3-opus",
    "claude-3-haiku",
    "claude-3-5-sonnet",
    "claude-3-7-sonnet",
    "claude-3-5-haiku",
    "claude-haiku-3-5",
    "claude-haiku-4-5",
    "claude-sonnet-4",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-opus-4",
    "claude-opus-4-1",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-mythos-preview",
    "claude-mythos-5",
    "claude-mythos-5-1",
}


def local(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    """Read only retained main-thread responses; quota is unrelated to context."""
    rows = conn.execute(
        """WITH ranked AS (
           SELECT *, input + cache_5m + cache_1h + cache_read AS prompt,
             ROW_NUMBER() OVER (PARTITION BY source, session ORDER BY ts DESC, msg_id DESC, request_id DESC) AS rank,
             MAX(input + cache_5m + cache_1h + cache_read) OVER (PARTITION BY source, session) AS peak
           FROM turns WHERE sidechain = 0 AND ts <= ?)
           SELECT * FROM ranked WHERE rank = 1 AND ts >= ? ORDER BY source, session""",
        (now.isoformat(), (now - timedelta(minutes=30)).isoformat()),
    )
    findings = []
    for row in rows:
        model = normalize(row["model"].replace("[1m]", ""))
        if model not in STANDARD_MODELS:
            continue
        maximum = (
            1_000_000 if "[1m]" in row["model"] or row["peak"] > 200_000 else 200_000
        )
        ratio = row["prompt"] / maximum
        if ratio <= 0.7:
            continue
        findings.append(
            {
                "session": row["session"],
                "source": row["source"],
                "used_tokens": row["prompt"],
                "max_tokens": maximum,
                "severity": "urgent" if ratio > 0.85 else "warning",
                "snapshot_at": row["ts"],
            }
        )
    return findings
