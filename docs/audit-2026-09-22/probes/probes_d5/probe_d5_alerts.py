"""d5 probes: burn/spike alerts (PLAN 7.5)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from vibewatt import store
from vibewatt.analysis.alerts import evaluate

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
T0 = datetime(2026, 9, 21, 0, tzinfo=UTC)


def sample(conn, at, util, reset, label="five_hour"):
    conn.execute(
        "INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?)",
        (at.isoformat(), label, util, reset.isoformat()),
    )


def response(conn, i, at, cost):
    conn.execute(
        "INSERT INTO turns (msg_id,request_id,ts,day,source,project,session,model,"
        "input,cache_5m,cache_1h,cache_read,output,thinking,web_search,sidechain,fast,geo,cost) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"m{i}", "r", at.isoformat(), at.date().isoformat(), "claude-code", "demo", "s",
         "claude-opus-5", 100, 0, 0, 0, 100, 0, 0, 0, 0, None, cost),
    )


def burn_ids(result):
    return {a["id"] for a in result["alerts"] if a["kind"] == "burn"}


def test_noisy_series_evaluated_per_sample_fires_once(tmp_path):
    rnd = random.Random(7)
    reset = T0 + timedelta(hours=5)
    total_new, ids = 0, set()
    with store.connect(tmp_path / "a.db") as conn:
        for k in range(60):  # every 5 min, rising ~2%/5min with noise -> crosses projection
            at = T0 + timedelta(minutes=5 * k)
            util = min(100.0, 10 + 1.6 * k + rnd.uniform(-3, 3))
            sample(conn, at, round(util, 1), reset)
            r = evaluate(conn, UTC, now=at + timedelta(seconds=1))
            total_new += r["new_count"]
            ids |= burn_ids(r)
    print("NOISY total_new", total_new, "distinct ids", len(ids))
    assert total_new == 1 and len(ids) == 1


def test_crossing_twice_same_window_and_new_window(tmp_path):
    reset1 = T0 + timedelta(hours=5)
    reset2 = T0 + timedelta(hours=10)
    seq = [(0, 10, reset1), (30, 40, reset1),  # projects >100 -> fire
           (60, 40, reset1),  # flat -> no projection
           (90, 70, reset1),  # crosses again
           (310, 5, reset2), (340, 50, reset2)]  # new window -> fires again once
    news = []
    with store.connect(tmp_path / "b.db") as conn:
        for minute, util, reset in seq:
            at = T0 + timedelta(minutes=minute)
            sample(conn, at, util, reset)
            news.append(evaluate(conn, UTC, now=at)["new_count"])
    print("CROSSING news", news)
    assert news == [0, 1, 0, 0, 0, 1]


def test_out_of_order_and_duplicate_timestamps(tmp_path):
    reset = T0 + timedelta(hours=5)
    with store.connect(tmp_path / "c.db") as conn:
        sample(conn, T0 + timedelta(minutes=60), 60, reset)
        sample(conn, T0, 20, reset)  # inserted later, older
        sample(conn, T0 + timedelta(minutes=60), 99, reset)  # duplicate ts ignored by PK
        r = evaluate(conn, UTC, now=T0 + timedelta(minutes=61))
        print("OOO", r["new_count"], [a["detail"] for a in r["alerts"]])
        assert r["new_count"] == 1  # 20 -> 60 in 1h, 4h left -> 220%


def test_units_percent_and_boundary(tmp_path):
    reset = T0 + timedelta(hours=2)
    with store.connect(tmp_path / "d.db") as conn:
        # 60 -> 80 in 1h, 1h to reset -> exactly 100 -> must NOT fire (> 100)
        sample(conn, T0 - timedelta(hours=1), 60, reset - timedelta(hours=0))
        sample(conn, T0, 80, reset - timedelta(hours=0))
        r = evaluate(conn, UTC, now=T0 + timedelta(minutes=0))
        # reset is 2h after T0 -> 80 + 20*2 = 120 -> fires
        print("UNITS", [a["detail"] for a in r["alerts"]])
        assert r["new_count"] == 1
    with store.connect(tmp_path / "e.db") as conn:
        # fractions 0-1 never fire: documents unit assumption
        sample(conn, T0 - timedelta(hours=1), 0.6, reset)
        sample(conn, T0, 0.8, reset)
        assert evaluate(conn, UTC, now=T0)["new_count"] == 0


def test_dense_integer_samples_slow_burn_spurious(tmp_path):
    """True burn 1% per 10 min from 30% with 4h left projects 54%. The endpoint
    reports whole percents and every dashboard request samples it."""
    reset = T0 + timedelta(hours=4, minutes=10)
    fired_at = []
    with store.connect(tmp_path / "f.db") as conn:
        for k in range(0, 40):  # a sample every 30 s for 20 minutes
            at = T0 + timedelta(seconds=30 * k)
            true = 30 + (30 * k) / 600  # 1% per 600 s
            sample(conn, at, float(int(true)), reset)
            r = evaluate(conn, UTC, now=at)
            if r["new_count"]:
                fired_at.append((k, r["alerts"][0]["detail"]))
    print("DENSE fired", fired_at)
    assert fired_at == []  # expected: a 54% true projection must not alert


def test_spike_exactly_once_and_threshold(tmp_path):
    with store.connect(tmp_path / "g.db") as conn:
        for i in range(50):
            response(conn, i, T0 + timedelta(minutes=i), 0.01)
        response(conn, 50, T0 + timedelta(minutes=50), 0.05)  # exactly 5x: no
        response(conn, 51, T0 + timedelta(minutes=51), 0.0501)  # > 5x: yes
        now = T0 + timedelta(minutes=52)
        first = evaluate(conn, UTC, now=now)
        second = evaluate(conn, UTC, now=now)
        print("SPIKE", first["new_count"], second["new_count"],
              [a["detail"] for a in first["alerts"]])
        assert first["new_count"] == 1 and second["new_count"] == 0


def test_alert_day_uses_report_tz(tmp_path):
    reset = T0 + timedelta(hours=5)
    now = datetime(2026, 9, 21, 23, 30, tzinfo=UTC)  # 2026-09-22 08:30 JST
    with store.connect(tmp_path / "h.db") as conn:
        sample(conn, now - timedelta(minutes=30), 40, now + timedelta(hours=4))
        sample(conn, now - timedelta(minutes=10), 60, now + timedelta(hours=4))
        evaluate(conn, JST, now=now)
        day = conn.execute("SELECT day FROM findings").fetchone()[0]
    print("DAY", day)
    assert day == "2026-09-22"


def test_one_overview_load_writes_several_samples(monkeypatch):
    from fastapi.testclient import TestClient

    from vibewatt import config, quota
    from vibewatt.api import create_app

    calls = []

    def fake_fetch(token=None, timeout=10.0):
        calls.append(1)
        return quota.Quota([quota.Window("5-hour", 37.0, datetime(2030, 1, 1, tzinfo=UTC))],
                           "endpoint", datetime.now(UTC))

    monkeypatch.setattr(quota, "read_token", lambda: "fake")
    monkeypatch.setattr(quota, "fetch", fake_fetch)
    c = TestClient(create_app({**config.DEFAULTS, "offline": True, "timezone": "utc"}))
    for path in ("/api/summary", "/api/quota", "/api/blocks"):  # Overview + Hero + UsageCharts
        assert c.get(path).status_code == 200
    with store.connect() as conn:
        rows = conn.execute("SELECT ts FROM quota_samples ORDER BY ts").fetchall()
    span = (datetime.fromisoformat(rows[-1][0]) - datetime.fromisoformat(rows[0][0])).total_seconds()
    print("SAMPLES per load", len(rows), "span s", span, "fetches", len(calls))
    assert len(rows) == 3


def test_burst_sampling_hides_real_burn(tmp_path):
    """Each dashboard load writes ~3 samples within ~30 ms (see previous probe).
    True burn 2%/5min from 20%, 4h left -> projects ~116%+ and should alert."""
    reset = T0 + timedelta(hours=4, minutes=30)
    total_new = 0
    with store.connect(tmp_path / "i.db") as conn:
        for load in range(6):  # a page load every 5 min
            base = T0 + timedelta(minutes=5 * load)
            util = 20.0 + 2 * load
            for j in range(3):  # summary, quota, blocks
                sample(conn, base + timedelta(milliseconds=10 * j), util, reset)
            total_new += evaluate(conn, UTC, now=base + timedelta(seconds=1))["new_count"]
    print("BURST total_new", total_new)
    assert total_new == 1


def test_concurrent_evaluations_fire_once(tmp_path):
    import threading

    path = tmp_path / "k.db"
    reset = T0 + timedelta(hours=5)
    with store.connect(path) as conn:
        sample(conn, T0, 10, reset)
        sample(conn, T0 + timedelta(minutes=30), 40, reset)
    results, barrier = [], threading.Barrier(8)

    def run():
        with store.connect(path) as conn:
            barrier.wait()
            results.append(evaluate(conn, UTC, now=T0 + timedelta(minutes=31))["new_count"])

    threads = [threading.Thread(target=run) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    print("CONCURRENT", results)
    assert sum(results) == 1 and len(results) == 8


def test_history_spikes_all_listed_as_active(tmp_path):
    rnd = random.Random(1)
    with store.connect(tmp_path / "l.db") as conn:
        for i in range(3000):  # ~200 days of responses, lognormal cost (sigma 1.0 assumed)
            at = T0 - timedelta(days=200) + timedelta(minutes=96 * i)
            response(conn, i, at, round(rnd.lognormvariate(-4, 1.0), 6))
        r = evaluate(conn, UTC, now=T0)
        spikes = [a for a in r["alerts"] if a["kind"] == "spike"]
    oldest = min(a["id"] for a in spikes) if spikes else None  # noqa: F841
    print("HISTORY spikes listed", len(spikes), "new", r["new_count"])
    assert len(spikes) < 20
