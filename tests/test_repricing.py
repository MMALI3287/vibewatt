"""Retained local costs follow pricing changes without needing the logs."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from vibewatt import pricing, store
from vibewatt.aggregate import cost_of
from vibewatt.sources import Turn


def seed(conn, model="claude-sonnet-5", key="m"):
    turn = Turn(
        "claude-code",
        datetime(2026, 9, 15, tzinfo=UTC),
        model,
        1_000_000,
        0,
        0,
        0,
        0,
        0,
        0,
        False,
        None,
        False,
        "demo",
        "s",
        (key, "r"),
    )
    store.upsert_turns(conn, [turn], UTC, cost_of)
    store.rebuild_rollup(conn)
    return turn


def test_override_reprices_retained_turns_and_rollup_preserving_cloud(tmp_path):
    with store.connect(tmp_path / "db") as conn:
        seed(conn)
        conn.execute(
            "INSERT INTO sessions (id, cost, harvested) VALUES ('cloud', 123, 1)"
        )
        store.reprice(conn)
        before = store.generation(conn)
        assert store.reprice(conn, {"claude-sonnet-5": {"input": 7}}) == 1
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 7
        assert conn.execute("SELECT cost FROM rollup").fetchone()[0] == 7
        assert conn.execute("SELECT cost FROM sessions").fetchone()[0] == 123
        assert store.generation(conn) == before + 1
        assert store.reprice(conn, {"claude-sonnet-5": {"input": 7}}) == 0
        assert store.generation(conn) == before + 1
        assert store.reprice(conn) == 1
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 2


def test_builtin_and_remote_updates_change_unpriced_status(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "_remote", {})
    with store.connect(tmp_path / "db") as conn:
        seed(conn, "unknown")
        store.reprice(conn)
        monkeypatch.setitem(pricing._remote, "unknown", pricing.Rate(4, 5, 8, 0.4, 20))
        assert store.reprice(conn) == 1
        assert conn.execute("SELECT cost, unpriced FROM rollup").fetchone()[:] == (4, 0)
        monkeypatch.setitem(
            pricing.BUILTIN, "unknown", pricing.Rate(6, 7.5, 12, 0.6, 30)
        )
        assert store.reprice(conn) == 1
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 6
        monkeypatch.delitem(pricing.BUILTIN, "unknown")
        monkeypatch.delitem(pricing._remote, "unknown")
        assert store.reprice(conn) == 1
        assert conn.execute("SELECT cost, unpriced FROM rollup").fetchone()[:] == (
            None,
            1,
        )


def test_repricing_preserves_ttl_fast_geo_and_search_charges(tmp_path, monkeypatch):
    with store.connect(tmp_path / "db") as conn:
        turn = seed(conn, "claude-opus-5")
        turn = replace(
            turn,
            cache_5m=1_000_000,
            cache_1h=1_000_000,
            cache_read=1_000_000,
            output=1_000_000,
            fast=True,
            geo="us",
            web_searches=3,
        )
        store.upsert_turns(conn, [turn], UTC, cost_of)
        store.rebuild_rollup(conn)
        store.reprice(conn)
        monkeypatch.setitem(
            pricing.FAST_MODE, "claude-opus-5", pricing.Rate(2, 3, 4, 5, 6)
        )
        assert store.reprice(conn) == 1
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == pytest.approx(
            22.03
        )


def test_failed_repricing_rolls_back_costs_and_fingerprint(tmp_path, monkeypatch):
    from vibewatt import aggregate

    with store.connect(tmp_path / "db") as conn:
        seed(conn)
        seed(conn, key="n")
        store.reprice(conn)
        snapshot = dict(conn.execute("SELECT key, value FROM meta"))
        original = aggregate.cost_of

        def failing(turn, overrides=None):
            if turn.key[0] == "n":
                raise RuntimeError("interrupted")
            return original(turn, overrides)

        monkeypatch.setattr(aggregate, "cost_of", failing)
        with pytest.raises(RuntimeError):
            store.reprice(conn, {"claude-sonnet-5": {"input": 9}})
        assert [r[0] for r in conn.execute("SELECT cost FROM turns")] == [2, 2]
        assert dict(conn.execute("SELECT key, value FROM meta")) == snapshot


def test_pricing_change_invalidates_savings_even_if_stored_cost_is_unchanged(tmp_path):
    with store.connect(tmp_path / "db") as conn:
        turn = seed(conn)
        conn.execute("DELETE FROM turns")
        store.upsert_turns(
            conn, [replace(turn, input=0, cache_read=1_000_000)], UTC, cost_of
        )
        store.rebuild_rollup(conn)
        store.reprice(conn)
        before = store.generation(conn)
        assert (
            store.reprice(conn, {"claude-sonnet-5": {"input": 8, "cache_read": 0.2}})
            == 0
        )
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 0.2
        assert store.generation(conn) == before + 1


def test_sync_store_reprices_unchanged_and_missing_logs(tmp_path):
    from vibewatt.cli import sync_store

    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-09-15T01:00:00Z",
                "sessionId": "session",
                "requestId": "request",
                "message": {
                    "id": "message",
                    "model": "claude-sonnet-5",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = {"offline": True}
    files = [("claude-code", path)]
    assert sync_store(cfg, UTC, files).parsed == 1
    cfg["pricing_overrides"] = {"claude-sonnet-5": {"input": 7}}
    result = sync_store(cfg, UTC, files)
    assert result.parsed == 0 and result.skipped == 1
    with store.connect() as conn:
        assert conn.execute("SELECT cost FROM rollup").fetchone()[0] == 7
    # Simulate transcript retention expiry; the retained store must still reprice.
    path.unlink()
    cfg["pricing_overrides"] = {"claude-sonnet-5": {"input": 9}}
    assert sync_store(cfg, UTC, []).parsed == 0
    with store.connect() as conn:
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 9
        assert conn.execute("SELECT cost FROM rollup").fetchone()[0] == 9


def _write_remote_cache(age_seconds: float) -> None:
    import os
    import time

    path = pricing._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "claude-3-7-sonnet-20250219": {
                    "litellm_provider": "anthropic",
                    "input_cost_per_token": 3e-06,
                    "output_cost_per_token": 1.5e-05,
                }
            }
        ),
        encoding="utf-8",
    )
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))


def test_stale_remote_cache_keeps_remote_only_costs_when_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "_remote", None)
    monkeypatch.setattr(pricing, "_fetch_remote", lambda *a, **k: None)
    _write_remote_cache(0)
    pricing.refresh(offline=True)
    with store.connect(tmp_path / "db") as conn:
        seed(conn, "claude-3-7-sonnet")
        store.reprice(conn)
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 3
        # The cache ages past its TTL while the network is unavailable.
        _write_remote_cache(pricing.CACHE_TTL_SECONDS * 3)
        pricing.refresh(offline=True)
        store.reprice(conn)
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 3
        pricing.refresh(offline=False)
        store.reprice(conn)
        assert conn.execute("SELECT cost FROM turns").fetchone()[0] == 3


def test_restored_history_is_priced_from_its_tokens_after_repricing(tmp_path):
    from datetime import date

    from vibewatt.aggregate import from_store

    overrides = {"claude-sonnet-5": {"input": 6}}
    with store.connect(tmp_path / "db") as conn:
        seed(conn)  # 1M input tokens on 2026-09-15
        conn.execute(
            "INSERT INTO history_days (day, model, responses, input, cost)"
            " VALUES ('2026-09-15', 'claude-sonnet-5', 2, 2000000, 4.0)"
        )
        store.reprice(conn, overrides)
        report = from_store(conn, UTC, overrides=overrides)
        assert report.by_day[date(2026, 9, 15)].input == 2_000_000
        assert report.by_day[date(2026, 9, 15)].cost == pytest.approx(12)
