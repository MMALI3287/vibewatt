from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vibewatt import cli


def test_local_timezone_retains_dst_rules(monkeypatch):
    import tzlocal

    monkeypatch.setattr(tzlocal, "get_localzone", lambda: ZoneInfo("America/New_York"))
    tz = cli.resolve_tz("local")
    assert datetime(2026, 1, 1, tzinfo=tz).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 7, 1, tzinfo=tz).utcoffset() == timedelta(hours=-4)


def test_unresolvable_local_zone_falls_back_instead_of_crashing(monkeypatch, capsys):
    # tzlocal raises ZoneInfoNotFoundError (a KeyError) for POSIX TZ strings
    # such as TZ=JST-9 and for conflicting system configs.
    from zoneinfo import ZoneInfoNotFoundError

    import tzlocal

    def unresolvable():
        raise ZoneInfoNotFoundError("tzlocal() does not support JST-9")

    monkeypatch.setattr(tzlocal, "get_localzone", unresolvable)
    tz = cli.resolve_tz("local")
    assert datetime(2026, 1, 1, tzinfo=tz).utcoffset() is not None
    assert cli.report_zone({"timezone": "local"}) is not None
    assert "local timezone" in capsys.readouterr().err
