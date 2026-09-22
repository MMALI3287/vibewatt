"""Only harvested snapshots carry a reliable context-window denominator."""

from __future__ import annotations

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
