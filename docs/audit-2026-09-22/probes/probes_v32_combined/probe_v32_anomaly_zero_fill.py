from __future__ import annotations
from datetime import date, timedelta
from vibewatt.analysis.anomaly import detect

def _rows(days, cost=5.0):
    return [{"day": d.isoformat(), "cost": cost} for d in days]

def test_two_days_a_week_flat():
    start = date(2026, 1, 5)  # Monday
    days = [start + timedelta(days=i) for i in range(84) if (start + timedelta(days=i)).weekday() in (0, 3)]
    f, enough = detect(_rows(days), None, days[-1])
    print("2d/wk evaluable-ish", enough, "findings", len(f), "of", len(days), f[0].metrics if f else None)
    assert len(f) == 0

def test_vacation_return():
    start = date(2026, 1, 1)
    active = [start + timedelta(days=i) for i in range(40)] + [start + timedelta(days=i) for i in range(60, 70)]
    f, _ = detect(_rows(active), None, active[-1])
    print("vacation findings", [x.metrics["median_usd"] for x in f], len(f))
    assert len(f) == 0

def test_daily_flat_none():
    start = date(2026, 1, 1)
    active = [start + timedelta(days=i) for i in range(30)]
    f, _ = detect(_rows(active), None, active[-1])
    assert len(f) == 0
