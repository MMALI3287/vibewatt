"""Phase 6.5d: quota sources, pace and band forecasting, alerts."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from vibewatt import cli, quota, store
from vibewatt.analysis.alerts import evaluate
from vibewatt.analysis.forecast import Point, episodes, forecast, forecast_series

UTC = timezone.utc
NOW = datetime(2026, 9, 21, 2, tzinfo=UTC)
FIVE = timedelta(hours=5)


def sample(conn, when, utilization, reset=None, key="five_hour", scope="account",
           source="statusline"):
    conn.execute(
        "INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?,?,?,?)",
        (when.isoformat(), key, quota.label_for(key), scope, float(utilization),
         reset.isoformat() if reset else None, source),
    )


def past_windows(conn, count, *, growth, key="five_hour"):
    """`count` finished 5-hour windows that each grew `growth` points after the midpoint."""
    for i in range(count):
        reset = NOW - timedelta(hours=6 * (i + 1))
        start = reset - FIVE
        sample(conn, start + timedelta(minutes=30), 10, reset, key)
        sample(conn, start + timedelta(hours=2, minutes=30), 40, reset, key)
        sample(conn, reset - timedelta(minutes=5), 40 + growth, reset, key)


@pytest.fixture
def conn(tmp_path):
    with store.connect(tmp_path / "alerts.db") as connection:
        yield connection


def response(conn, index, cost, minutes_ago):
    conn.execute(
        f"INSERT INTO turns ({store.TURN_COLUMNS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"m{index}", "r", (NOW - timedelta(minutes=minutes_ago)).isoformat(), "2026-09-21",
         "claude-code", "demo", "s", "unknown", 100, 0, 0, 0, 100, 0, 0, 0, 0, None, cost,
         None, 1),
    )


# --- the gate -----------------------------------------------------------------------

def test_burst_sampled_series_crossing_100_fires_exactly_once(conn):
    reset = NOW + timedelta(hours=2)
    fired = 0
    stamp = NOW - timedelta(hours=3)
    for value in [20, 20, 21, 45, 45, 46, 70, 71, 71, 90, 99, 100, 100, 100]:
        for _ in range(3):  # a burst of identical refreshes
            stamp += timedelta(seconds=20)
            sample(conn, stamp, value, reset)
            fired += evaluate(conn, UTC, now=stamp)["new_count"]
    assert fired == 1
    assert evaluate(conn, UTC, now=stamp + timedelta(minutes=1))["new_count"] == 0


def test_noisy_flat_series_fires_nothing(conn):
    past_windows(conn, 6, growth=2)
    rng = random.Random(3)
    # A window starts at 0 %, so a flat series is flat mid-window: here from
    # 20 % of the way in, when past windows had also reached 40 %.
    reset = NOW + timedelta(hours=2)
    fired = 0
    for i in range(60):
        stamp = NOW - timedelta(hours=2) + timedelta(minutes=2 * i)
        sample(conn, stamp, 40 + rng.choice([-1, 0, 1]), reset)
        fired += evaluate(conn, UTC, now=stamp)["new_count"]
    assert fired == 0


def test_band_never_below_the_current_value(conn):
    past_windows(conn, 6, growth=0)
    sample(conn, NOW - timedelta(minutes=5), 77, NOW + timedelta(hours=2))
    (window,) = forecast(conn, NOW)
    assert window["band"] is not None
    assert min(window["band"].values()) >= window["used"] == 77


def test_projection_under_100_does_not_fire(conn):  # A-053
    past_windows(conn, 6, growth=10)
    sample(conn, NOW - timedelta(minutes=5), 50, NOW + timedelta(hours=2, minutes=30))
    (window,) = forecast(conn, NOW)
    assert window["band"]["p50"] == pytest.approx(60)
    assert evaluate(conn, UTC, now=NOW)["alerts"] == []


def test_history_projecting_past_100_fires_once(conn):
    past_windows(conn, 6, growth=50)
    reset = NOW + timedelta(hours=2, minutes=30)
    sample(conn, NOW - timedelta(minutes=5), 60, reset)
    first = evaluate(conn, UTC, now=NOW)
    assert first["new_count"] == 1
    assert "past windows put the reset" in first["alerts"][0]["detail"]
    sample(conn, NOW - timedelta(minutes=1), 61, reset)
    assert evaluate(conn, UTC, now=NOW)["new_count"] == 0


def test_not_enough_history_is_said(conn):
    past_windows(conn, 2, growth=10)
    sample(conn, NOW - timedelta(minutes=5), 50, NOW + timedelta(hours=2))
    (window,) = forecast(conn, NOW)
    assert window["band"] is None and "not enough history: 2 of 4" in window["note"]
    assert window["pace_delta"] == pytest.approx(50 - 60)


def test_resets_are_detected_from_the_data():
    t0 = NOW - timedelta(hours=10)
    points = [Point(t0 + timedelta(minutes=15 * i), u, None)
              for i, u in enumerate([5, 30, 60, 90, 8, 20, 35])]
    assert [len(e.points) for e in episodes(points, FIVE)] == [4, 3]
    far = [Point(t0, 40, None), Point(t0 + timedelta(hours=6), 45, None)]
    assert len(episodes(far, FIVE)) == 2


def test_series_without_a_current_window_has_no_forecast():
    old = [Point(NOW - timedelta(hours=9), 50, NOW - timedelta(hours=4))]
    assert forecast_series("five_hour", "account", old, NOW) is None


# --- sources ------------------------------------------------------------------------

STATUSLINE = {  # Claude Code 2.1.80+ statusline stdin, trimmed
    "session_id": "abc", "model": {"id": "claude-opus-5", "display_name": "Opus"},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": 1_900_000_000},
        "seven_day": {"used_percentage": 41.2, "resets_at": 1_900_300_000},
    },
}


def test_statusline_stdin_is_recorded_and_throttled():
    raw = json.dumps(STATUSLINE)
    line = cli.statusline({"quota": True}, UTC, raw)
    assert "5-hour 24%" in line and "7-day 41%" in line
    cli.statusline({"quota": True}, UTC, raw)  # the same reading seconds later
    with store.connect() as c:
        rows = [tuple(r) for r in c.execute(
            "SELECT key, utilization, resets_at, source FROM quota_samples ORDER BY key")]
    assert rows == [
        ("five_hour", 23.5, "2030-03-17T17:46:40+00:00", "statusline"),
        ("seven_day", 41.2, "2030-03-21T05:06:40+00:00", "statusline"),
    ]


def test_statusline_chains_the_users_command_and_survives_bad_input():
    import sys

    chain = f'"{sys.executable}" -c "import sys; print(len(sys.stdin.read()))"'
    raw = json.dumps(STATUSLINE)
    line = cli.statusline({"quota": True, "statusline_chain": chain}, UTC, raw)
    assert line.startswith(str(len(raw)))
    assert cli.statusline({"quota": True}, UTC, "not json") == ""


def test_statusline_is_fast():
    import time

    raw = json.dumps(STATUSLINE)
    cli.statusline({"quota": True}, UTC, raw)  # first call creates the store
    started = time.perf_counter()
    cli.statusline({"quota": True}, UTC, raw)
    assert time.perf_counter() - started < 0.05


def test_stale_dump_is_not_a_sample(tmp_path):  # A-013
    import os

    dump = tmp_path / "rate-limits.json"
    dump.write_text(json.dumps({"rate_limits": {"five_hour": {"used_percentage": 42}}}),
                    encoding="utf-8")
    old = (NOW - timedelta(hours=2)).timestamp()
    os.utime(dump, (old, old))
    assert quota.from_statusline(dump, now=NOW) is None
    fresh = quota.from_statusline(
        dump, now=datetime.fromtimestamp(old, UTC) + timedelta(minutes=1))
    assert fresh is not None and fresh.windows[0].utilization == 42


def test_desktop_history_import_is_version_gated_and_deduped(tmp_path, conn):
    path = tmp_path / "plan-usage-history.json"
    blob = {"version": 2, "samples": [
        {"t": 1_789_000_000_000, "org": "org-1", "u": {"fh": 37, "sd": 34}},
        {"t": 1_789_000_900_000, "org": "org-1", "u": {"fh": 50, "sd": 35}},
    ]}
    path.write_text(json.dumps(blob), encoding="utf-8")
    assert quota.import_desktop(conn, path) == (4, "")
    assert quota.import_desktop(conn, path) == (0, "")
    path.write_text(json.dumps({**blob, "version": 3}), encoding="utf-8")
    assert quota.import_desktop(conn, path) == (
        0, "desktop plan history version 3 not supported")


def test_one_desktop_org_and_the_account_are_one_series(conn):
    sample(conn, NOW - timedelta(minutes=30), 40, scope="org:o1", source="desktop")
    sample(conn, NOW - timedelta(minutes=1), 44, NOW + timedelta(hours=1))
    latest = quota.latest(conn, now=NOW)
    assert [(w.key, w.utilization) for w in latest.windows] == [("five_hour", 44.0)]
    assert [w["scope"] for w in forecast(conn, NOW)] == ["org:o1"]


def test_endpoint_is_throttled_and_backs_off(conn, monkeypatch):
    calls = []

    def limited():
        calls.append(1)
        raise quota.RateLimited

    monkeypatch.setattr(quota, "read_token", lambda: "t")
    monkeypatch.setattr(quota, "fetch", limited)
    cfg = {"quota": True}
    assert "backing off" in quota.maybe_fetch(conn, cfg, NOW)
    assert "paused" in quota.maybe_fetch(conn, cfg, NOW + timedelta(minutes=5))
    assert len(calls) == 1
    quota.maybe_fetch(conn, cfg, NOW + timedelta(minutes=11))
    assert "paused" in quota.maybe_fetch(conn, cfg, NOW + timedelta(minutes=25))
    assert len(calls) == 2  # the second 429 doubled the wait to 20 minutes

    later = NOW + timedelta(hours=7)
    reading = quota.Quota([quota.Window("five_hour", 10, later + timedelta(hours=4))],
                          "endpoint", later)
    monkeypatch.setattr(quota, "fetch", lambda: reading)
    assert quota.maybe_fetch(conn, cfg, later) == ""
    monkeypatch.setattr(quota, "fetch", lambda: pytest.fail("fetched while fresh"))
    assert quota.maybe_fetch(conn, cfg, later + timedelta(minutes=1)) == ""


def test_quota_endpoint_never_calls_the_network(monkeypatch):
    from fastapi.testclient import TestClient

    from vibewatt.api import create_app

    monkeypatch.setattr(quota, "read_token", lambda: "t")
    monkeypatch.setattr(quota, "fetch",
                        lambda *a: pytest.fail("a page load called the endpoint"))
    client = TestClient(create_app({"offline": False, "quota": True}))
    client.app.state.synced = True
    assert client.get("/api/quota").json() is None
    with store.connect() as c:
        sample(c, datetime.now(UTC), 33, datetime.now(UTC) + timedelta(hours=1))
    body = client.get("/api/quota").json()
    assert body["windows"][0]["utilization"] == 33
    assert body["windows"][0]["note"].startswith("not enough history")
    assert body["recent"][0]["utilization"] == 33


# --- spikes and blocks ----------------------------------------------------------------

@pytest.mark.parametrize("count,cost,expected", [(49, 10, 0), (50, 5, 0), (50, 6, 1)])
def test_spike_baseline_and_durable_dedup(conn, count, cost, expected):
    for index in range(count):
        response(conn, index, 1, 100 - index)
    response(conn, count, None, 100 - count)
    response(conn, count + 1, cost, 100 - count - 1)
    store.rebuild_rollup(conn)
    result = evaluate(conn, UTC, now=NOW)
    assert result["new_count"] == expected
    assert evaluate(conn, UTC, now=NOW)["new_count"] == 0
    assert result["active_block"]["cost_per_minute"] == pytest.approx((count + cost) / 120)


def test_spike_older_than_a_day_is_not_an_active_alert(conn):  # A-050
    for index in range(50):
        response(conn, index, 1, 3000 - index)
    response(conn, 50, 100, 2900)  # a spike two days ago
    store.rebuild_rollup(conn)
    assert evaluate(conn, UTC, now=NOW)["alerts"] == []


def test_alert_day_is_in_the_report_timezone(conn):  # A-096
    from zoneinfo import ZoneInfo

    when = NOW - timedelta(hours=3)          # 23:00 UTC on the 20th, 08:00 JST on the 21st
    sample(conn, when - timedelta(minutes=1), 100, NOW + timedelta(hours=1))
    evaluate(conn, ZoneInfo("Asia/Tokyo"), now=when)
    (row,) = conn.execute("SELECT day FROM findings WHERE kind = 'burn'").fetchall()
    assert row[0] == "2026-09-21"


def test_store_errors_do_not_break_the_status_line(monkeypatch):
    def broken(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "connect", broken)
    assert "5-hour" in cli.statusline({"quota": True}, UTC, json.dumps(STATUSLINE))
