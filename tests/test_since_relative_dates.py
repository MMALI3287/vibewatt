"""Relative --since dates must follow report timezones, including month boundaries."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from vibewatt import cli


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("7d", date(2026, 2, 21)),
        ("2w", date(2026, 2, 14)),
        ("this-month", date(2026, 2, 1)),
        ("last-month", date(2026, 1, 1)),
    ],
)
def test_since_uses_report_zone_across_utc_month_boundary(expression, expected):
    now = datetime(2026, 3, 1, 1, 0, tzinfo=UTC)
    assert (
        cli._parse_since_date(expression, ZoneInfo("America/Los_Angeles"), now=now)
        == expected
    )


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("7d", date(2026, 2, 22)),
        ("2w", date(2026, 2, 15)),
        ("this-month", date(2026, 3, 1)),
        ("last-month", date(2026, 2, 1)),
    ],
)
def test_since_uses_ahead_of_utc_report_zone(expression, expected):
    now = datetime(2026, 3, 1, 1, 0, tzinfo=UTC)
    assert (
        cli._parse_since_date(expression, ZoneInfo("Asia/Tokyo"), now=now) == expected
    )


def test_since_keeps_iso_date_and_crosses_year_boundary():
    now = datetime(2026, 1, 3, tzinfo=UTC)
    assert cli._parse_since_date("2026-02-28", UTC, now=now) == date(2026, 2, 28)
    assert cli._parse_since_date("last-month", UTC, now=now) == date(2025, 12, 1)


@pytest.mark.parametrize(
    "invalid",
    [
        "0d",
        "0w",
        "-7d",
        "7x",
        "this-week",
        "last-year",
        "2026-02-30",
        "01d",
        "9" * 5000,
    ],
)
def test_since_rejects_unsupported_forms_with_cli_error(invalid):
    with pytest.raises(ValueError, match="--since expects YYYY-MM-DD"):
        cli._parse_since_date(invalid, UTC, now=datetime(2026, 3, 1, tzinfo=UTC))


def test_json_cli_uses_relative_cutoff_in_report_timezone(monkeypatch, capsys):
    fixed = datetime(2026, 3, 1, 1, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed

    seen = []
    monkeypatch.setattr(cli, "datetime", FrozenDatetime)
    monkeypatch.setattr(cli.pricing, "refresh", lambda **_kwargs: None)

    def fake_build_report(cfg, tz, *, source, date_from, refresh):
        seen.append((tz, date_from))
        return object(), None, None, 0, ["synthetic"]

    monkeypatch.setattr(cli, "build_report", fake_build_report)
    monkeypatch.setattr(cli, "serialize", lambda _report: {"synthetic": True})
    args = cli.build_parser().parse_args(
        ["json", "--since", "this-month", "--tz", "America/Los_Angeles"]
    )
    assert cli._main(args, {"offline": True}) == 0
    assert seen[0][1] == date(2026, 2, 1)
    assert '"synthetic": true' in capsys.readouterr().out.lower()


def test_invalid_since_exits_before_report(monkeypatch):
    monkeypatch.setattr(cli.pricing, "refresh", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "build_report",
        lambda *_args, **_kwargs: pytest.fail("must not build report"),
    )
    args = cli.build_parser().parse_args(
        ["json", "--since", "not-a-date", "--tz", "utc"]
    )
    with pytest.raises(SystemExit, match="--since expects YYYY-MM-DD"):
        cli._main(args, {"offline": True})


def test_since_respects_custom_report_day_start():
    from vibewatt.config import day_zone

    # March 1 at 05:00 New York time still belongs to February with a 06:00 boundary.
    zone = day_zone(ZoneInfo("America/New_York"), 6)
    now = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    assert cli._parse_since_date("this-month", zone, now=now) == date(2026, 2, 1)
    assert cli._parse_since_date("last-month", zone, now=now) == date(2026, 1, 1)
