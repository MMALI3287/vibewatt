from __future__ import annotations

from datetime import date, timedelta, timezone

from fastapi.testclient import TestClient

from ccburn import cli as climod
from ccburn import config as configmod
from ccburn.api import create_app

JST = timezone(timedelta(hours=9))


def _cfg():
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    return cfg


def test_build_report_buckets_days_and_hours_in_report_tz(logs):
    report, *_ = climod.build_report(_cfg(), JST)
    days = {d.isoformat() for d in report.by_day}
    # fixture claude-code turns are 20:00Z and 21:00Z on 09-15 -> 09-16 05:00/06:00 JST
    assert "2026-09-16" in days
    assert 5 in report.by_hour and 6 in report.by_hour
    assert 20 not in report.by_hour


def test_build_report_date_range_inclusive_in_report_tz(logs):
    d16 = date(2026, 9, 16)
    only16, *_ = climod.build_report(_cfg(), JST, date_from=d16, date_to=d16,
                                     source="claude-code")
    assert only16.total.turns == 2
    upto15, *_ = climod.build_report(_cfg(), JST, date_to=date(2026, 9, 15),
                                     source="claude-code")
    assert upto15.total.turns == 0


def test_api_daily_from_to_in_report_tz(logs):
    app = create_app(_cfg())
    app.state.tz = JST
    c = TestClient(app)
    body = c.get("/api/daily?from=2026-09-16&to=2026-09-16&source=claude-code").json()
    assert list(body) == ["2026-09-16"]
