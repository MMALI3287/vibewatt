"""d6 probes: PLAN section 10 traps outside the core pipeline."""
from __future__ import annotations

import datetime as dt

from vibewatt import terminal
from vibewatt.aggregate import Bucket, Report

FILLED = "\u25a0"


def _fixed(day):
    class _D(dt.date):
        @classmethod
        def today(cls):
            return cls(day.year, day.month, day.day)
    return _D


def test_cli_heatmap_uses_system_date_not_report_today(monkeypatch):
    report_today = dt.date(2026, 9, 21)  # Monday in the report timezone (JST)
    report = Report()
    report.today = report_today
    b = Bucket()
    b.input = 1000
    report.by_day[report_today] = b
    monkeypatch.setattr(terminal, "date", _fixed(dt.date(2026, 9, 20)))  # system: Sunday
    grid = terminal.heatmap(report, weeks=4, color=False).split("Less")[0]
    print("system-date grid:", ascii(grid))
    assert FILLED not in grid  # today's usage in the report tz is not drawn
    monkeypatch.setattr(terminal, "date", _fixed(report_today))
    control = terminal.heatmap(report, weeks=4, color=False).split("Less")[0]
    print("control filled:", control.count(FILLED))
    assert control.count(FILLED) == 1


def test_cloud_cost_kept_verbatim_but_missing_cost_becomes_zero():
    from vibewatt import store

    with store.connect() as conn:
        store.upsert_cloud_sessions(conn, [
            {"id": "c-cost", "created_at": "2026-09-15T00:00:00Z", "model": "claude-opus-5",
             "external_metadata": {"usage": {"input_tokens": 1000, "output_tokens": 10,
                                             "cache_write_tokens": 999, "cost_usd": 25.362093}}},
            {"id": "c-nocost", "created_at": "2026-09-15T00:00:00Z",
             "external_metadata": {"usage": {"input_tokens": 2_000_000, "output_tokens": 50_000}}},
        ])
        rows = {r["id"]: r for r in store.sessions(conn, limit=10)}
    print({k: (v["cost"], v["tokens"], v["unpriced_turns"]) for k, v in rows.items()})
    assert rows["c-cost"]["cost"] == 25.362093  # API cost_usd, not recomputed
    assert rows["c-nocost"]["cost"] == 0.0 and rows["c-nocost"]["unpriced_turns"] == 0
