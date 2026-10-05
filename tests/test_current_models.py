"""Phase 9: models released before Phase 8 closed were missing from every table."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_repricing import seed

from vibewatt import pricing, store
from vibewatt.aggregate import cost_of
from vibewatt.analysis import context

ONE_M = 1_000_000


@pytest.mark.parametrize(
    "model,rate",
    [
        ("claude-opus-5-5", pricing.Rate(4, 5, 8, 0.20, 20)),
        ("claude-sonnet-5-5", pricing.Rate(2, 2.50, 4, 0.20, 10)),
    ],
)
def test_current_models_are_priced_per_field(tmp_path, monkeypatch, model, rate):
    monkeypatch.setattr(pricing, "_remote", {})
    with store.connect(tmp_path / "db") as conn:
        turn = seed(conn, model)
        for field, expected in zip(
            ("input", "cache_5m", "cache_1h", "cache_read", "output"), rate, strict=True
        ):
            only = replace(
                turn, **{f: (ONE_M if f == field else 0) for f in rate._fields}
            )
            assert cost_of(only) == pytest.approx(expected)


@pytest.mark.parametrize("stamp,expected", [("2026-09-23", None), ("2026-09-24", 8.0)])
def test_opus_five_five_fast_mode_starts_at_launch(tmp_path, stamp, expected):
    with store.connect(tmp_path / "db") as conn:
        turn = replace(
            seed(conn, "claude-opus-5-5"),
            fast=True,
            ts=datetime.fromisoformat(stamp).replace(tzinfo=UTC),
        )
        assert cost_of(turn) == expected
        if expected:
            # Opus 5.5 cache reads are 0.05x base input, fast or not.
            read = replace(turn, input=0, cache_read=ONE_M)
            assert cost_of(read) == pytest.approx(0.40)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5"])
def test_current_models_have_no_long_context_premium(tmp_path, model):
    with store.connect(tmp_path / "db") as conn:
        turn = replace(
            seed(conn, model), input=900_000, ts=datetime(2026, 10, 2, tzinfo=UTC)
        )
        assert not pricing.context_premium_unknown(turn)


@pytest.mark.parametrize(
    "model,used,expected",
    [
        # First-party ids of native-1M models: 180K is 18%, not a nudge.
        ("claude-opus-5-5", 180_000, []),
        ("claude-sonnet-5", 180_000, []),
        ("claude-opus-4-7", 180_000, []),
        ("claude-opus-5-5", 800_000, [1_000_000]),
        # Provider ids may run at 200K (Bedrock, Vertex), so no native 1M.
        ("us.anthropic.claude-opus-4-8-v1:0", 180_000, [200_000]),
        ("claude-opus-4-8@20260528", 180_000, [200_000]),
        # Opus 4.6 reaches 1M only through its [1m] variant.
        ("claude-opus-4-6", 180_000, [200_000]),
    ],
)
def test_context_nudge_uses_native_window(tmp_path, model, used, expected):
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with store.connect(tmp_path / "db") as conn:
        base = seed(conn, model)
        conn.execute("DELETE FROM turns")
        store.upsert_turns(
            conn,
            [replace(base, input=used, ts=now - timedelta(minutes=1))],
            UTC,
            cost_of,
        )
        assert [f["max_tokens"] for f in context.local(conn, now)] == expected


def test_every_priced_model_has_a_context_window():
    # A rate added without a window silently disables context nudges for it.
    assert set(pricing.BUILTIN) <= context.STANDARD_MODELS
