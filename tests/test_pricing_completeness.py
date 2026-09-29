from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_repricing import seed

from vibewatt import pricing, store
from vibewatt.aggregate import cost_of


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-3-5-sonnet-20240620", 3),
        ("claude-3-5-sonnet-20241022", 3),
        ("claude-3-7-sonnet-20250219", 3),
        ("claude-mythos-preview", 25),
    ],
)
def test_new_builtin_rates_reprice_retained_rows(
    tmp_path, monkeypatch, model, expected
):
    monkeypatch.setattr(pricing, "_remote", {})
    with store.connect(tmp_path / "db") as conn:
        seed(conn, model)
        conn.execute("UPDATE turns SET cost = NULL")
        store.rebuild_rollup(conn)
        assert store.reprice(conn) == 1
        assert conn.execute("SELECT cost, unpriced FROM rollup").fetchone()[:] == (
            expected,
            0,
        )


@pytest.mark.parametrize(
    "model,stamp,expected",
    [
        ("claude-opus-4-6", "2026-05-11", None),
        ("claude-opus-4-6", "2026-06-28", 30),
        ("claude-opus-4-6", "2026-06-29", None),
        ("claude-opus-4-7", "2026-05-11", None),
        ("claude-opus-4-7", "2026-05-12", 30),
        ("claude-opus-4-7", "2026-07-23", 30),
        ("claude-opus-4-7", "2026-07-24", None),
    ],
)
def test_fast_cost_uses_response_date(tmp_path, model, stamp, expected):
    with store.connect(tmp_path / "db") as conn:
        turn = seed(conn, model)
        turn = replace(
            turn, fast=True, ts=datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        )
        assert cost_of(turn) == expected


@pytest.mark.parametrize(
    "size,expected", [(199999, 0.999995), (200000, 1.0), (200001, 2.00001)]
)
def test_historical_long_context_threshold(tmp_path, size, expected):
    with store.connect(tmp_path / "db") as conn:
        turn = replace(
            seed(conn, "claude-opus-4-6"),
            input=size,
            ts=datetime(2026, 3, 12, tzinfo=UTC),
        )
        assert cost_of(turn) == pytest.approx(expected)
        assert cost_of(
            replace(turn, ts=datetime(2026, 3, 13, tzinfo=UTC))
        ) == pytest.approx(size * 5 / 1_000_000)


def test_tool_counts_and_advisor_guard_survive_dedup_and_store(tmp_path):
    from pathlib import Path

    from vibewatt.aggregate import from_store
    from vibewatt.sources import dedupe, read_file

    turns, dropped = dedupe(
        read_file("claude-code", Path("tests/fixtures/server_tools.jsonl"))
    )
    assert dropped == 1
    assert turns[0].web_fetch == 3
    assert turns[0].code_execution == 2
    assert turns[0].nonstandard_iterations == 1
    assert turns[0].input == 100
    with store.connect(tmp_path / "db") as conn:
        store.upsert_turns(conn, turns, UTC, cost_of)
        store.rebuild_rollup(conn)
        report = from_store(conn, UTC)
        assert report.total.web_fetch == 3
        assert report.total.code_execution == 2
        assert report.total.nonstandard_iterations == 1
        assert report.total.input == 100
        assert report.total.cost == pytest.approx(0.0002)


def test_doctor_warns_on_nonstandard_iterations(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from vibewatt import doctor
    from vibewatt.sources import dedupe, read_file

    turns, _ = dedupe(
        read_file("claude-code", Path("tests/fixtures/server_tools.jsonl"))
    )
    with store.connect() as conn:
        store.upsert_turns(conn, turns, UTC, cost_of)
        store.rebuild_rollup(conn)
    monkeypatch.setattr(
        doctor,
        "discover",
        lambda cfg: [("claude-code", Path("tests/fixtures/server_tools.jsonl"))],
    )
    doctor.run({"quota": False, "offline": True}, UTC)
    assert "nonstandard_iterations: 1" in capsys.readouterr().out


def test_migration_backs_up_prior_schema_before_altering(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    for version, migration in store.MIGRATIONS.items():
        if version < 10:
            migration(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '9')")
    conn.commit()
    conn.close()
    with store.connect(path) as conn:
        assert store.schema_version(conn) == store.SCHEMA_VERSION
    backups = list(tmp_path.glob(f"old.db.pre-v{store.SCHEMA_VERSION}-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as before:
        assert (
            before.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]
            == "9"
        )


@pytest.mark.parametrize(
    "size,expected", [(199999, 0.599997), (200000, 0.6), (200001, 1.200006)]
)
def test_sonnet_four_historical_threshold(tmp_path, size, expected):
    with store.connect(tmp_path / "db") as conn:
        turn = replace(
            seed(conn, "claude-sonnet-4"),
            input=size,
            ts=datetime(2025, 8, 12, tzinfo=UTC),
        )
        assert cost_of(turn) == pytest.approx(expected)


@pytest.mark.parametrize(
    "model,stamp", [("claude-opus-4-8", "2026-05-27"), ("claude-opus-5", "2026-07-23")]
)
def test_current_fast_models_do_not_price_prelaunch_turns(tmp_path, model, stamp):
    with store.connect(tmp_path / "db") as conn:
        turn = replace(
            seed(conn, model),
            fast=True,
            ts=datetime.fromisoformat(stamp).replace(tzinfo=UTC),
        )
        assert cost_of(turn) is None


def test_historical_fast_analysis_uses_same_rate_as_store(tmp_path):
    from vibewatt.analysis import cache_scan, tips

    with store.connect(tmp_path / "db") as conn:
        turn = replace(
            seed(conn, "claude-opus-4-7"),
            fast=True,
            ts=datetime(2026, 6, 1, tzinfo=UTC),
        )
        store.upsert_turns(conn, [turn], UTC, cost_of)
        row = dict(conn.execute("SELECT * FROM turns").fetchone())
        assert cache_scan.detect("s", [row], None)[0].savings_usd == 27
        fast_tip = next(
            f for f in tips.detect("s", [row], [], None) if f.rule == "fast_mode"
        )
        assert fast_tip.savings_usd == 25
