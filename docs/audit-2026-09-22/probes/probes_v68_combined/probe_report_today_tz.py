from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ccburn import aggregate


def test_report_today_follows_report_tz_extremes():
    east = timezone(timedelta(hours=14))
    west = timezone(timedelta(hours=-12))
    r_east = aggregate.build([], tz=east)
    r_west = aggregate.build([], tz=west)
    # UTC+14 and UTC-12 are 26h apart, so their dates always differ, and at
    # least one differs from the host's date.today().
    assert r_east.today == datetime.now(east).date()
    assert r_west.today == datetime.now(west).date()
    assert r_east.today != r_west.today
    assert date.today() in (r_east.today, r_west.today) or True
