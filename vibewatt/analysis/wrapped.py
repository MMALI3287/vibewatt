"""Year review with explicit retained-store and live-log coverage."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date, timedelta, tzinfo

from .. import cli, store
from ..pricing import MILLION, rate_for
from .models import local_day, timestamp, total_tokens


def build(
    conn: sqlite3.Connection,
    cfg: dict,
    tz: tzinfo,
    year: int,
    *,
    source: str = "all",
    project: str | None = None,
    model: str | None = None,
) -> dict:
    """Preserve summary parity without treating retained/cloud usage as live logs."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    report, *_ = cli.build_report(
        {**cfg, "quota": False},
        tz,
        date_from=start,
        date_to=end,
        source=source,
        project=project,
        model=model,
    )
    rows = []
    all_local_ids = set(report.sessions)
    for raw in conn.execute("SELECT * FROM turns ORDER BY ts, msg_id, request_id"):
        row = dict(raw)
        all_local_ids.add(row["session"])
        day = local_day(row["ts"], tz)
        if (
            not day
            or not start.isoformat() <= day <= end.isoformat()
            or (source != "all" and row["source"] != source)
            or (project and row["project"] != project)
            or (model and row["model"] != model)
            or (row["sidechain"] and not cfg.get("include_sidechains", True))
        ):
            continue
        row["day"] = day
        rows.append(row)
    sessions = store.sessions(
        conn,
        limit=2**31 - 1,
        source=None if source == "all" else source,
        project=project,
        model=model,
        date_from=start,
        date_to=end,
        tz=tz,
    )
    clouds = [s for s in sessions if s["harvested"] and s["id"] not in all_local_ids]
    projects: dict[str, dict] = {}
    months: dict[tuple[str, str], dict] = {}
    daily: dict[str, int] = defaultdict(int)
    hourly: dict[int, int] = defaultdict(int)
    local_sessions: dict[str, dict] = {}
    titles = {s["id"]: s["title"] for s in sessions}
    savings = 0.0
    unpriced = 0

    def add(name: str, model_name: str, day: str, tokens: int, cost: float) -> None:
        item = projects.setdefault(name, {"name": name, "cost_usd": 0.0, "tokens": 0})
        item["cost_usd"] += cost
        item["tokens"] += tokens
        key = (day[:7], model_name)
        item = months.setdefault(
            key, {"month": key[0], "model": model_name, "cost_usd": 0.0, "tokens": 0}
        )
        item["cost_usd"] += cost
        item["tokens"] += tokens
        daily[day] += tokens

    for row in rows:
        tokens, cost = total_tokens(row), row["cost"] or 0.0
        add(row["project"], row["model"], row["day"], tokens, cost)
        stamp = timestamp(row["ts"])
        hourly[stamp.astimezone(tz).hour] += tokens
        item = local_sessions.setdefault(
            row["session"],
            {
                "id": row["session"],
                "title": titles.get(row["session"], row["session"]),
                "cost_usd": 0.0,
                "tokens": 0,
                "harvested": False,
            },
        )
        item["cost_usd"] += cost
        item["tokens"] += tokens
        rate = rate_for(
            row["model"],
            fast=bool(row["fast"]),
            geo=row["geo"],
            overrides=cfg.get("pricing_overrides"),
        )
        unpriced += row["cost"] is None
        if rate is None:
            savings = math.nan
        else:
            savings += row["cache_read"] * (rate.input - rate.cache_read) / MILLION
    for row in clouds:
        add(
            row["project"] or "-",
            row["model"] or "Unknown",
            local_day(row["started"], tz),
            row["tokens"],
            row["cost"],
        )
    candidates = list(local_sessions.values()) + [
        {
            "id": s["id"],
            "title": s["title"],
            "cost_usd": s["cost"],
            "tokens": s["tokens"],
            "harvested": True,
        }
        for s in clouds
    ]
    days = sorted(daily)
    longest = run = 0
    previous = None
    for day in days:
        current = date.fromisoformat(day)
        run = run + 1 if previous and current - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = current
    stored_cost = sum(s["cost_usd"] for s in candidates)
    plan = cfg.get("plan_usd_per_month")
    annual = (
        float(plan) * 12
        if isinstance(plan, (int, float)) and math.isfinite(plan) and plan > 0
        else None
    )
    aliases = cfg.get("project_aliases") or {}
    ranked = sorted(projects.values(), key=lambda p: (-p["tokens"], p["name"]))
    for index, item in enumerate(ranked, 1):
        item["name"] = (
            f"project {index}"
            if cfg.get("mask_projects")
            else aliases.get(item["name"], item["name"])
        )
    return {
        "year": year,
        "timezone": str(tz),
        "local_summary": cli.serialize(report),
        "stored_cost_usd": round(stored_cost, 6),
        "harvested_cost_usd": round(sum(s["cost"] for s in clouds), 6),
        "stored_tokens": sum(s["tokens"] for s in candidates),
        "stored_sessions": len(candidates),
        "unpriced_turns": unpriced,
        "annual_plan_usd": annual,
        "api_equivalent_multiple": stored_cost / annual
        if annual and not unpriced
        else None,
        "busiest_day": max(days, key=lambda d: daily[d]) if days else None,
        "busiest_hour": max(sorted(hourly), key=lambda h: hourly[h])
        if hourly
        else None,
        "longest_streak": longest,
        "top_projects": ranked[:10],
        "model_months": [months[k] for k in sorted(months)],
        "biggest_session": max(candidates, key=lambda s: (s["tokens"], s["id"]))
        if candidates
        else None,
        "cache_savings_usd": round(savings, 6) if math.isfinite(savings) else None,
        "notes": [
            "Live-log totals match /api/summary for this calendar year. Retained-store totals are separate and require Sync.",
            "Stored totals include retained local turns and non-overlapping harvested sessions. They are not account-wide.",
            "Busiest and biggest mean most tokens. Hour and cache savings cover local turns only; cloud usage is attributed to its start day.",
            "Cloud API costs are unchanged. Unknown local costs are excluded, not priced at zero.",
            "The API-equivalent multiple uses 12 months at the configured monthly plan price, not actual subscription payments.",
        ],
    }
