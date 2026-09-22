"""d4 probes: 7.4 anomaly detection math, adversarial fixtures."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from ccburn import store
from ccburn.analysis import analyze, anomaly

UTC = timezone.utc
START = date(2026, 6, 1)


def row(day: date, cost):
    return {"day": day.isoformat(), "cost": cost}


def test_spec_verify_30_flat_plus_10x_exactly_one():
    rows = [row(START + timedelta(days=i), 1.0) for i in range(30)]
    rows.append(row(START + timedelta(days=30), 10.0))
    findings, enough = anomaly.detect(rows, None, START + timedelta(days=30))
    print("30 flat + 10x ->", len(findings), enough)
    assert enough and len(findings) == 1


def test_spec_verify_30_flat_none():
    rows = [row(START + timedelta(days=i), 1.0) for i in range(30)]
    findings, enough = anomaly.detect(rows, None, START + timedelta(days=29))
    print("30 flat ->", len(findings), enough)
    assert enough and not findings


def test_mad_zero_one_percent_bump_is_flagged():
    rows = [row(START + timedelta(days=i), 1.0) for i in range(30)]
    rows.append(row(START + timedelta(days=30), 1.01))
    findings, _ = anomaly.detect(rows, None, START + timedelta(days=30))
    print("MAD=0, 1.01x day ->", len(findings), [f.metrics for f in findings])
    assert len(findings) == 1  # documents the false positive


def test_two_days_a_week_every_active_day_is_anomaly():
    # Active Monday and Thursday only, identical $5 each day, 12 weeks.
    rows = []
    d = START  # 2026-06-01 is a Monday
    assert d.weekday() == 0
    active = 0
    for i in range(84):
        day = d + timedelta(days=i)
        if day.weekday() in (0, 3):
            rows.append(row(day, 5.0))
            active += 1
    end = d + timedelta(days=83)
    findings, enough = anomaly.detect(rows, None, end)
    evaluated = [
        r for r in rows if (date.fromisoformat(r["day"]) - d).days >= 14
    ]
    print(
        f"2 days/week flat $5: active_days={active} evaluable={len(evaluated)} "
        f"anomalies={len(findings)} enough={enough}"
    )
    print("first metrics:", findings[0].metrics if findings else None)
    assert len(findings) == len(evaluated)


def test_weekday_user_random_costs_false_positive_rate():
    rng = random.Random(42)
    rows = []
    for i in range(365):
        day = START + timedelta(days=i)
        if day.weekday() < 5:
            rows.append(row(day, round(rng.lognormvariate(1.5, 0.35), 2)))
    end = START + timedelta(days=364)
    findings, _ = anomaly.detect(rows, None, end)
    evaluable = [r for r in rows if (date.fromisoformat(r["day"]) - START).days >= 14]
    print(
        f"weekday user (lognormal, sigma 0.35) 1 year: evaluable={len(evaluable)} "
        f"anomalies={len(findings)} rate={len(findings) / len(evaluable):.1%}"
    )
    # same distribution, active every day
    rows7 = [
        row(START + timedelta(days=i), round(rng.lognormvariate(1.5, 0.35), 2))
        for i in range(365)
    ]
    f7, _ = anomaly.detect(rows7, None, end)
    print(f"daily user same dist: evaluable=351 anomalies={len(f7)} rate={len(f7) / 351:.1%}")


def insert(conn, day: date, cost, model="claude-opus-5", n=0):
    conn.execute(
        "INSERT INTO turns (msg_id, request_id, ts, day, source, project, session, model,"
        " input, cache_5m, cache_1h, cache_read, output, thinking, web_search, sidechain,"
        " fast, geo, cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"m{day}{n}", "r", f"{day.isoformat()}T12:00:00+00:00", day.isoformat(),
            "claude-code", "demo", f"s{day}", model, 10, 0, 0, 0, 10, 0, 0, 0, 0, None, cost,
        ),
    )


def test_not_enough_history_note_false_with_60_days_history():
    now = datetime(2026, 8, 15, 12, tzinfo=UTC)
    with store.connect() as conn:
        for i in range(60):
            insert(conn, date(2026, 6, 1) + timedelta(days=i), 1.0)
        # 2026-08-15 has no activity; 60 days of history exist before it.
        result = analyze(conn, UTC, date_from=date(2026, 8, 15), date_to=date(2026, 8, 15), now=now)
        notes = [n for n in result["notes"] if "Not enough history" in n]
        print("empty-day range with 60d history ->", notes)
        assert notes


def test_not_enough_history_note_when_real_cause_is_unpriced_outside_range():
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    with store.connect() as conn:
        for i in range(90):
            insert(conn, date(2026, 6, 1) + timedelta(days=i), 1.0)
        # One unknown-model turn 20 days before the viewed week.
        insert(conn, date(2026, 8, 3), None, model="mystery-model", n=1)
        result = analyze(conn, UTC, date_from=date(2026, 8, 24), date_to=date(2026, 8, 29), now=now)
        print("notes:", [n[:70] for n in result["notes"]])
        assert any("Not enough history" in n for n in result["notes"])
        assert not any("Unpriced local models" in n for n in result["notes"])


def test_vacation_return_days_flagged():
    # Daily $5 for 60 days, 15-day break, then 10 more days at the same $5.
    rows = [row(START + timedelta(days=i), 5.0) for i in range(60)]
    back = START + timedelta(days=75)
    rows += [row(back + timedelta(days=i), 5.0) for i in range(10)]
    findings, _ = anomaly.detect(rows, None, back + timedelta(days=9))
    print("vacation: flagged days after return:", [f.day for f in findings],
          [f.metrics["median_usd"] for f in findings][:3])
    assert findings


def test_date_filtered_view_uses_preceding_history_and_report_tz():
    now = datetime(2026, 8, 31, 12, tzinfo=UTC)
    jst = timezone(timedelta(hours=9))
    with store.connect() as conn:
        for i in range(60):
            insert(conn, date(2026, 6, 20) + timedelta(days=i), 1.0)
        # 2026-08-25T20:00Z is 2026-08-26 in JST
        conn.execute(
            "INSERT INTO turns (msg_id, request_id, ts, day, source, project, session, model,"
            " input, cache_5m, cache_1h, cache_read, output, thinking, web_search, sidechain,"
            " fast, geo, cost) VALUES ('spike','r','2026-08-25T20:00:00+00:00','2026-08-25',"
            "'claude-code','demo','sx','claude-opus-5',1,0,0,0,1,0,0,0,0,NULL,20.0)")
        res = analyze(conn, jst, date_from=date(2026, 8, 24), date_to=date(2026, 8, 30), now=now)
    an = [f for f in res["findings"] if f["kind"] == "anomaly"]
    print("filtered-range anomalies:", [(f["day"], f["metrics"]["baseline_days"]) for f in an])
    assert [f["day"] for f in an] == ["2026-08-26"]


def test_float_summation_noise_flags_identical_spend_when_mad_zero():
    rows = [row(START + timedelta(days=i), 0.3) for i in range(30)]
    d = START + timedelta(days=30)
    rows += [row(d, 0.1), row(d, 0.2)]  # same $0.30, two responses
    findings, _ = anomaly.detect(rows, None, d)
    print("0.1+0.2 vs flat 0.3 ->", [(f.day, f.metrics["cost_usd"], f.metrics["threshold_usd"]) for f in findings])
    assert len(findings) == 1
