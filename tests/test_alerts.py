from datetime import datetime, timedelta, timezone

import pytest

from ccburn import store
from ccburn.analysis.alerts import evaluate

UTC = timezone.utc
NOW = datetime(2026, 9, 21, 2, tzinfo=UTC)


def sample(conn, minutes, utilization, reset=None):
    conn.execute(
        "INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?)",
        (
            (NOW + timedelta(minutes=minutes)).isoformat(),
            "five_hour",
            utilization,
            (reset or NOW + timedelta(hours=3)).isoformat(),
        ),
    )


def response(conn, index, cost):
    conn.execute(
        "INSERT INTO turns (msg_id,request_id,ts,day,source,project,session,model,"
        "input,cache_5m,cache_1h,cache_read,output,thinking,web_search,sidechain,fast,geo,cost) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"m{index}",
            "r",
            (NOW - timedelta(minutes=100 - index)).isoformat(),
            "2026-09-21",
            "claude-code",
            "demo",
            "s",
            "unknown",
            100,
            0,
            0,
            0,
            100,
            0,
            0,
            0,
            0,
            None,
            cost,
        ),
    )


def test_burn_fires_once_across_samples_and_restart(tmp_path):
    path = tmp_path / "alerts.db"
    with store.connect(path) as conn:
        sample(conn, -20, 40)
        sample(conn, -10, 50)
        assert evaluate(conn, UTC, now=NOW)["new_count"] == 1
        sample(conn, -5, 60)
        sample(conn, -5, 60)
        assert evaluate(conn, UTC, now=NOW)["new_count"] == 0
    with store.connect(path) as conn:
        assert evaluate(conn, UTC, now=NOW)["new_count"] == 0
        assert evaluate(conn, UTC, now=NOW + timedelta(hours=4))["alerts"] == []
        sample(conn, 230, 40, NOW + timedelta(hours=8))
        sample(conn, 235, 60, NOW + timedelta(hours=8))
        assert evaluate(conn, UTC, now=NOW + timedelta(hours=4))["new_count"] == 1


@pytest.fixture
def conn(tmp_path):
    with store.connect(tmp_path / "alerts.db") as connection:
        yield connection


@pytest.mark.parametrize("values", [[40], [40, 40], [60, 40], [40, -1], [40, 101]])
def test_burn_requires_valid_rising_evidence(conn, values):
    for index, value in enumerate(values):
        sample(conn, -20 + index * 10, value)
    assert evaluate(conn, UTC, now=NOW)["alerts"] == []


def test_burn_no_cross_reset_or_future_evidence(conn):
    sample(conn, -20, 40, NOW + timedelta(hours=2))
    sample(conn, -10, 60)
    sample(conn, 10, 80)
    assert evaluate(conn, UTC, now=NOW)["alerts"] == []


@pytest.mark.parametrize("count,cost,expected", [(49, 10, 0), (50, 5, 0), (50, 6, 1)])
def test_spike_baseline_and_durable_dedup(conn, count, cost, expected):
    for index in range(count):
        response(conn, index, 1)
    response(conn, count, None)
    response(conn, count + 1, cost)
    result = evaluate(conn, UTC, now=NOW)
    assert result["new_count"] == expected
    assert evaluate(conn, UTC, now=NOW)["new_count"] == 0
    assert result["active_block"]["cost_per_minute"] == pytest.approx(
        (count + cost) / 120
    )
