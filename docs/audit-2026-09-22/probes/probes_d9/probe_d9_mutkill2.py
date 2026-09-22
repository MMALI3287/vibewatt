"""d9 kill-probes for extra survivors X1, X3, X5, X6, X8, X13, X20."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from ccburn import aggregate, config, history, sources
from ccburn.aggregate import Bucket, Report
from ccburn.sources import Turn

UTC = timezone.utc
JST = timezone(timedelta(hours=9))


def turn(ts, key):
    return Turn(source="claude-code", ts=ts, model="claude-sonnet-4-5", input=1, cache_5m=0,
                cache_1h=0, cache_read=0, output=1, thinking=0, web_searches=0, fast=False,
                geo=None, sidechain=False, project="p", session="s", key=key)


def test_build_report_date_to_is_inclusive(logs):
    from ccburn import cli
    cfg = {**config.DEFAULTS, "offline": True, "quota": False}
    report, *_ = cli.build_report(cfg, JST, date_to=date(2026, 9, 16))
    assert report.total.turns == 3


def test_block_starts_on_the_hour():
    blocks = aggregate.build_blocks([turn(datetime(2026, 9, 10, 10, 30, tzinfo=UTC), ("a", "1")),
                                     turn(datetime(2026, 9, 10, 15, 10, tzinfo=UTC), ("b", "2"))])
    assert [b.start.hour for b in blocks] == [10, 15]


def test_idless_turns_are_not_collapsed(tmp_path):
    f = tmp_path / "s.jsonl"
    rec = lambda ts: json.dumps({"type": "assistant", "timestamp": ts, "sessionId": "s",
                                 "message": {"model": "claude-sonnet-4-5",
                                             "usage": {"input_tokens": 1, "output_tokens": 1}}})
    f.write_text(rec("2026-09-10T03:00:00Z") + "\n" + rec("2026-09-10T04:00:00Z") + "\n", encoding="utf-8")
    turns, dups = sources.load([("claude-code", f)])
    assert (len(turns), dups) == (2, 0)


def test_history_restore_live_day_wins():
    r = Report()
    r.by_day[date(2026, 9, 10)].turns = 1
    blob = {"days": {"2026-09-10|claude-opus-5": {"responses": 99, "cost": 99.0}}}
    assert history.restore(r, blob) == 0
    assert r.by_day[date(2026, 9, 10)].turns == 1


def test_plan_multiple_is_api_over_plan():
    r = Report()
    r.today = date(2026, 9, 20)
    r.by_day[date(2026, 9, 5)].cost = 100.0
    assert aggregate.plan_comparison(r, 20)["multiple"] == pytest.approx(5.0)


def test_cache_to_output_needs_200k():
    from ccburn.analysis import waste
    row = {"day": "2026-09-10", "ts": "2026-09-10T03:00:00+00:00", "input": 0, "cache_5m": 0,
           "cache_1h": 0, "cache_read": 150_000, "output": 1, "sidechain": 0}
    assert [f for f in waste.detect("s", [row], []) if f.rule == "cache_to_output"] == []


def test_cache_hit_rate_counts_writes_in_denominator():
    b = Bucket(input=25, cache_5m=25, cache_read=50)
    assert b.cache_hit_rate == pytest.approx(0.5)
