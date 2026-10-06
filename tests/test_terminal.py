from __future__ import annotations

import sys

import pytest

from vibewatt import cli, terminal


@pytest.mark.parametrize(
    "force_color,no_color,is_tty,expected",
    [
        (None, None, False, False),
        (None, None, True, True),
        ("", None, False, False),
        ("", None, True, True),
        ("1", None, False, True),
        ("1", "", False, True),
        (None, "1", True, False),
        ("1", "1", False, False),
        ("1", "1", True, False),
    ],
)
def test_use_color(monkeypatch, force_color, no_color, is_tty, expected):
    for name, value in (("FORCE_COLOR", force_color), ("NO_COLOR", no_color)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: is_tty)

    assert terminal.use_color() is expected


@pytest.mark.parametrize(
    "no_color,flags,expected",
    [("", [], True), ("1", [], False), ("", ["--no-color"], False)],
)
def test_forced_color_in_piped_report(
    monkeypatch, logs, capsys, no_color, flags, expected
):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    assert cli.main(["report", "--offline", "--no-quota", *flags]) == 0

    assert ("\x1b[" in capsys.readouterr().out) is expected
