"""Versioned store-only CLI snapshots for automation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

from . import pricing, quota, store
from .config import zone_id


class ResyncRequired(ValueError):
    """Stored calendar buckets cannot be read under a different timezone."""


def run(args: argparse.Namespace, cfg: dict, tz: tzinfo) -> int:
    """Emit schema v1; exit 0 available, 1 partial/unavailable, 2 error."""
    from .cli import build_report, serialize

    payload = {
        "schema_version": 1,
        "command": args.command,
        "generated_at": datetime.now(UTC).isoformat(),
        "state": "unavailable",
        "usage": None,
        "quota": {
            "state": "unavailable",
            "reason": "disabled" if not cfg.get("quota", True) else "no_current_sample",
            "fetched_at": None,
            "windows": [],
        },
        "error": None,
    }
    code = 1
    try:
        with store.connect() as conn:
            reading = quota.latest(conn) if cfg.get("quota", True) else None
            synced = conn.execute(
                "SELECT value FROM meta WHERE key='last_sync'"
            ).fetchone()
            synced_zone = conn.execute(
                "SELECT value FROM meta WHERE key='sync_tz'"
            ).fetchone()
            has_usage = conn.execute("SELECT 1 FROM turns LIMIT 1").fetchone()
            if (
                args.command == "status"
                and has_usage
                and (not synced_zone or synced_zone[0] != zone_id(tz))
            ):
                raise ResyncRequired(
                    f"Store uses {synced_zone[0] if synced_zone else 'unknown timezone'}, "
                    f"requested {zone_id(tz)}. "
                    "Run sync with the requested timezone and day-start hour first."
                )
        if reading is not None:
            payload["quota"] = {
                "state": "available",
                "reason": None,
                "fetched_at": reading.fetched_at.isoformat(),
                "windows": [
                    {
                        "key": w.key,
                        "label": w.label,
                        "utilization_percent": w.utilization,
                        "resets_at": w.resets_at.isoformat() if w.resets_at else None,
                        "scope": w.scope,
                        "source": w.source,
                    }
                    for w in reading.windows
                ],
            }
        if args.command == "status":
            cutoff = (
                (datetime.now(tz) - timedelta(days=args.days)).date()
                if args.days
                else None
            )
            if args.since:
                cutoff = date.fromisoformat(args.since)
            # Offline refresh reads only the on-disk cache, so restored history
            # for remote-priced models matches `vibewatt json` without a fetch.
            pricing.refresh(offline=True)
            report, *_ = build_report(
                cfg,
                tz,
                source=args.source,
                date_from=cutoff,
                refresh=False,
                with_quota=False,
            )
            payload["usage"] = {
                "state": "available" if report.by_day else "unavailable",
                "reason": None if report.by_day else "no_retained_usage",
                "coverage": "retained_local",
                "account_wide": False,
                "provenance": "computed_local",
                "cost_is_estimate": True,
                "as_of": synced[0] if synced else None,
                "last_sync_at": synced[0] if synced else None,
                "report_date": report.today.isoformat(),
                "timezone": zone_id(tz),
                "report": serialize(report),
            }
            available = bool(report.by_day)
            payload["state"] = (
                "available"
                if available and reading is not None and not report.unknown_models
                else "partial"
                if available or reading is not None
                else "unavailable"
            )
        else:
            payload["state"] = payload["quota"]["state"]
        code = 0 if payload["state"] == "available" else 1
    except (sqlite3.Error, OSError, ValueError) as exc:
        payload["state"] = "error"
        payload["error"] = {
            "code": "resync_required"
            if isinstance(exc, ResyncRequired)
            else "snapshot_failed",
            "message": str(exc),
        }
        code = 2
    text = (
        json.dumps(payload, indent=2)
        if args.json
        else f"{args.command}: {payload['state']}"
        + (f" ({payload['error']['message']})" if payload["error"] else "")
    )
    if args.out:
        try:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            payload["state"] = "error"
            payload["error"] = {"code": "output_failed", "message": str(exc)}
            print(
                json.dumps(payload, indent=2) if args.json else f"{args.command}: {exc}"
            )
            return 2
    else:
        print(text)
    return code
