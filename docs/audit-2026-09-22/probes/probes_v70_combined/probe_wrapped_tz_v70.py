from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from vibewatt import config, store
import vibewatt.analysis.wrapped as wrapped
import tests.test_wrapped as orig

JST = timezone(timedelta(hours=9))
SRC = Path(wrapped.__file__).read_text()
MUTANTS = {
    "M22_day_utc": ('day = local_day(row["ts"], tz)', 'day = local_day(row["ts"], timezone.utc)'),
    "M22b_hour_utc": ("hourly[stamp.astimezone(tz).hour]", "hourly[stamp.hour]"),
}


def load_mutant(name):
    old, new = MUTANTS[name]
    assert SRC.count(old) == 1
    code = SRC.replace(old, new).replace(
        "from datetime import date, timedelta, tzinfo",
        "from datetime import date, timedelta, tzinfo, timezone")
    spec = importlib.util.spec_from_loader("vibewatt.analysis._mut", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "vibewatt.analysis"
    exec(compile(code, "mut", "exec"), mod.__dict__)
    return mod


def seed(conn):
    # 2026-03-01T16:00Z..2026-03-03T16:00Z are 01:00 JST on Mar 2,3,4; UTC days Mar 1,2,3.
    rows = []
    for i, ts in enumerate(["2026-03-01T16:30:00+00:00", "2026-03-02T16:30:00+00:00",
                            "2026-03-03T16:30:00+00:00", "2026-03-05T14:30:00+00:00"]):
        rows.append((f"m{i}", "r", ts, ts[:10], "claude-code", "p", f"s{i}",
                     "claude-opus-5", 100 if i < 3 else 10, 0, 0, 0, 0, 0, 0, 0, 0, None, 0.01))
    conn.executemany("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def run(build):
    with store.connect() as conn:
        seed(conn)
        return build(conn, {**config.DEFAULTS, "offline": True, "quota": False}, JST, 2026)


def test_jst_bucketing_real():
    r = run(wrapped.build)
    # JST days: Mar 2,3,4 (100 each) and Mar 5 23:30 JST (10) -> streak 4
    assert r["longest_streak"] == 4
    assert r["busiest_day"] == "2026-03-02"
    assert r["busiest_hour"] == 1


@pytest.mark.parametrize("name", list(MUTANTS))
def test_mutant_killed_by_jst_probe(name):
    r = run(load_mutant(name).build)
    assert (r["longest_streak"], r["busiest_day"], r["busiest_hour"]) != (4, "2026-03-02", 1)
    print(name, r["longest_streak"], r["busiest_day"], r["busiest_hour"])


@pytest.mark.parametrize("name", list(MUTANTS))
def test_mutant_survives_existing_suite(name, logs, monkeypatch):
    mod = load_mutant(name)
    monkeypatch.setattr(wrapped, "build", mod.build)
    import vibewatt.api.app as app_mod
    for attr in dir(app_mod):
        if getattr(app_mod, attr) is wrapped.build:
            monkeypatch.setattr(app_mod, attr, mod.build)
    # Every existing wrapped test that could see the tz; they must still pass under the mutant.
    orig.test_wrapped_cloud_overlap_and_timezone(logs)
