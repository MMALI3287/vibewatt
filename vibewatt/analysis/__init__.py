"""Deterministic analysis of stored evidence. No log discovery or network IO."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, tzinfo

from .. import store
from . import anomaly, cache_scan, context, peaks, tips, waste
from .models import local_day


def analyze(
    conn: sqlite3.Connection,
    tz: tzinfo,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str = "all",
    project: str | list[str] | None = None,
    model: str | None = None,
    overrides: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Persist a filter-specific snapshot; metric changes do not change evidence."""
    today = (now or datetime.now(tz)).astimezone(tz).date()
    end = min(date_to, today) if date_to else today
    scope = hashlib.sha256(
        json.dumps(
            [str(date_from), str(date_to), source, project, model],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    def facets(row: dict, source_key: str = "source") -> bool:
        return (
            (source == "all" or row[source_key] == source)
            and (project is None or row["project"] in (
                [project] if isinstance(project, str) else project))
            and (not model or row["model"] == model)
        )

    def in_range(day: str | None) -> bool:
        return bool(
            day
            and (not date_from or day >= date_from.isoformat())
            and day <= end.isoformat()
        )

    turns = []
    for row in conn.execute("SELECT * FROM turns ORDER BY ts, msg_id, request_id"):
        item = dict(row)
        item["day"] = local_day(item["ts"], tz)
        if item["day"] and facets(item):
            turns.append(item)
    findings, enough = anomaly.detect(turns, date_from, end)
    sessions: dict[str, list[dict]] = defaultdict(list)
    for row in turns:
        if in_range(row["day"]):
            sessions[row["session"]].append(row)
    read_groups: dict[str, list[dict]] = defaultdict(list)
    for row in conn.execute("SELECT * FROM tool_reads ORDER BY ts, tool_id"):
        item = dict(row)
        item["day"] = local_day(item["ts"], tz)
        if facets(item) and in_range(item["day"]):
            read_groups[item["session"]].append(item)
    for session in sorted(sessions.keys() | read_groups.keys()):
        rows = sessions[session]
        evidence = cache_scan.detect(session, rows, overrides) if rows else []
        evidence.extend(waste.detect(session, rows, read_groups[session]))
        findings.extend(evidence)
        if rows:
            findings.extend(tips.detect(session, rows, evidence, overrides))
    harvested = []
    for row in conn.execute("SELECT * FROM sessions WHERE harvested = 1"):
        item = dict(row)
        # Cloud sessions use their start date, as in the existing session API.
        item["day"] = local_day(item["started"], tz)
        if facets(item, "surface") and in_range(item["day"]):
            harvested.append(item)
    findings.extend(context.detect(harvested))
    notes = [
        (
            "Local usage rules cover only stored local responses matching these filters. "
            "Session checks use the matching portion of each session. Pruned logs may leave gaps."
        ),
        (
            "Local context is unavailable: usage blocks have no reliable context-window maximum. "
            "Context warnings use the latest harvested snapshots only."
        ),
        (
            "File-read checks use Read tool metadata collected during Sync. Run Sync once after "
            "upgrading to backfill retained logs; deleted transcripts cannot be recovered."
        ),
        (
            "Savings are separate scenarios and overlap. Do not add them together. "
            "Dismissals apply to this filter selection and survive refreshes."
        ),
    ]
    if not enough:
        notes.append(
            "Not enough history for anomaly detection: a fully priced day needs at "
            "least 14 preceding calendar days of observed history (up to 28)."
        )
    if source != "all" or project is not None or model:
        notes.append(
            "Peak-window checks are unavailable with source, project or model filters: "
            "quota samples are account-wide and cannot be attributed to those filters."
        )
    else:
        samples = [dict(r) for r in conn.execute("SELECT * FROM quota_samples")]
        peak_findings = peaks.detect(samples, tz, date_from, end)
        findings.extend(peak_findings)
        if not peak_findings:
            notes.append(
                "No repeated peak window established. At least two distinct limit "
                "encounters on different dates at the same weekday/hour are needed."
            )
    unknown = sorted(
        {r["model"] for rows in sessions.values() for r in rows if r["cost"] is None}
    )
    if unknown:
        notes.append(
            f"Unpriced local models: {', '.join(unknown)}. Incomplete daily costs "
            "are excluded from anomaly checks; unavailable savings remain unpriced."
        )
    # Tool-only records can exist without the usage or harvested rows required
    # by the session-detail endpoint. Keep their evidence without a broken link.
    available_sessions = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT session FROM turns UNION SELECT id FROM sessions WHERE harvested = 1"
        )
    }
    payload = []
    for finding in findings:
        data = finding.to_dict()
        if data["session_id"] not in available_sessions:
            data["session_id"] = None
        data["id"] = hashlib.sha256(
            json.dumps(
                [scope, finding.rule, finding.subject], separators=(",", ":")
            ).encode()
        ).hexdigest()
        payload.append(data)
    saved = store.save_findings(conn, scope, payload)
    priority = {"urgent": 0, "warning": 1, "info": 2}
    saved.sort(
        key=lambda f: (priority[f["severity"]], -(f["savings_usd"] or 0), f["id"])
    )
    return {
        "findings": saved,
        "notes": notes,
        "analyzed_at": (now or datetime.now(tz)).isoformat(),
    }
