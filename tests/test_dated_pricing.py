"""Rates in effect when a response happened, never the latest table applied backwards."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vibewatt import pricing

MODEL = "claude-opus-9"


def _write_cache(input_per_m: float, day: str) -> None:
    path = pricing._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                MODEL: {
                    "litellm_provider": "anthropic",
                    "input_cost_per_token": input_per_m / 1e6,
                    "output_cost_per_token": input_per_m * 5 / 1e6,
                    "max_input_tokens": 1_000_000,
                }
            }
        ),
        encoding="utf-8",
    )
    stamp = datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()
    os.utime(path, (stamp, stamp))


@pytest.fixture
def clean(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not access network")

    monkeypatch.setattr(pricing, "_fetch_remote", forbidden)
    monkeypatch.setattr(pricing, "_remote", None)
    monkeypatch.setattr(pricing, "_history", {})
    monkeypatch.setattr(pricing, "_windows", {})


def _at(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


def test_a_discount_applies_only_from_the_day_it_is_observed(clean):
    _write_cache(10, "2026-10-01")
    pricing.refresh(offline=True)
    _write_cache(5, "2026-10-10")
    pricing.refresh(offline=True)
    assert pricing.rate_for(MODEL, ts=_at("2026-09-01")).input == 10
    assert pricing.rate_for(MODEL, ts=_at("2026-10-09")).input == 10
    assert pricing.rate_for(MODEL, ts=_at("2026-10-10")).input == 5
    # The discount ending is just another observed change.
    _write_cache(10, "2026-10-20")
    pricing.refresh(offline=True)
    assert pricing.rate_for(MODEL, ts=_at("2026-10-15")).input == 5
    assert pricing.rate_for(MODEL, ts=_at("2026-10-21")).input == 10


def test_history_survives_a_restart_and_unchanged_rates_add_nothing(clean):
    _write_cache(10, "2026-10-01")
    pricing.refresh(offline=True)
    _write_cache(5, "2026-10-10")
    pricing.refresh(offline=True)
    pricing.refresh(offline=True)
    pricing._history, pricing._remote = {}, None
    pricing.refresh(offline=True)
    assert [day for day, _ in pricing._history[MODEL]] == ["2026-10-01", "2026-10-10"]


def test_history_is_bounded_per_model(clean):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(pricing.HISTORY_PER_MODEL + 5):
        _write_cache(1 + i, (start + timedelta(days=i)).date().isoformat())
        pricing.refresh(offline=True)
    kept = pricing._history[MODEL]
    assert len(kept) == pricing.HISTORY_PER_MODEL
    # The earliest observation anchors old usage, so it is never dropped.
    assert kept[0][0] == "2026-01-01"
    assert kept[-1][1].input == pricing.HISTORY_PER_MODEL + 5


def test_history_changes_the_pricing_fingerprint(clean):
    _write_cache(10, "2026-10-01")
    pricing.refresh(offline=True)
    before = pricing.fingerprint()
    _write_cache(5, "2026-10-10")
    pricing.refresh(offline=True)
    assert pricing.fingerprint() != before


def test_builtin_periods_price_by_date(monkeypatch):
    monkeypatch.setitem(
        pricing.BUILTIN_PERIODS,
        "claude-sonnet-5",
        (("2026-08-01", "2026-08-15", pricing.Rate(1, 1.25, 2, 0.1, 5)),),
    )
    assert pricing.rate_for("claude-sonnet-5", ts=_at("2026-08-10")).input == 1
    assert pricing.rate_for("claude-sonnet-5", ts=_at("2026-08-15")).input == 2


def test_remote_only_model_gets_its_listed_window(clean):
    _write_cache(10, "2026-10-01")
    pricing.refresh(offline=True)
    assert pricing.remote_window(MODEL) == 1_000_000
    assert pricing.remote_window("claude-sonnet-5") is None


def test_cache_age(clean):
    assert pricing.cache_age_days(now=_at("2026-10-01")) is None
    _write_cache(10, "2026-10-01")
    assert pricing.cache_age_days(now=_at("2026-10-09")) == pytest.approx(8)


def test_doctor_warns_on_a_stale_price_cache(clean, monkeypatch, capsys):
    from vibewatt import doctor

    _write_cache(10, "2020-01-01")
    doctor.pricing_cache_lines(lambda line="": print(line))
    assert "WARNING pricing cache" in capsys.readouterr().out
    _write_cache(10, datetime.now(UTC).date().isoformat())
    doctor.pricing_cache_lines(lambda line="": print(line))
    assert "WARNING" not in capsys.readouterr().out


def test_drift_report_lists_missing_and_mismatched_models():
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    from pricing_drift import drift

    payload = {
        "claude-opus-9": {
            "litellm_provider": "anthropic",
            "input_cost_per_token": 1e-05,
            "output_cost_per_token": 5e-05,
        },
        "claude-sonnet-5-20260706": {
            "litellm_provider": "anthropic",
            "input_cost_per_token": 3e-06,
            "output_cost_per_token": 1e-05,
        },
        "claude-sonnet-5-5": {
            "litellm_provider": "anthropic",
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 1e-05,
            "cache_read_input_token_cost": 2e-07,
        },
        "bedrock/claude-opus-9": {
            "litellm_provider": "bedrock",
            "input_cost_per_token": 9e-05,
            "output_cost_per_token": 9e-05,
        },
    }
    report = drift(payload, pricing.BUILTIN)
    assert any("claude-opus-9" in line and "missing" in line for line in report)
    assert any("claude-sonnet-5" in line and "input" in line for line in report)
    assert not any("claude-sonnet-5-5" in line for line in report)
    assert not any("bedrock" in line for line in report)


def test_context_nudge_uses_remote_window_for_unlisted_models(clean, tmp_path):
    from dataclasses import replace

    from test_repricing import seed

    from vibewatt import store
    from vibewatt.aggregate import cost_of
    from vibewatt.analysis import context

    _write_cache(10, "2026-10-01")
    pricing.refresh(offline=True)
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with store.connect(tmp_path / "db") as conn:
        base = seed(conn, MODEL)
        conn.execute("DELETE FROM turns")
        store.upsert_turns(
            conn,
            [
                replace(base, input=180_000, ts=now, session="a", key=("a", "r")),
                replace(base, input=800_000, ts=now, session="b", key=("b", "r")),
                replace(
                    base,
                    model="claude-unknown-1",
                    input=190_000,
                    ts=now,
                    session="c",
                    key=("c", "r"),
                ),
            ],
            UTC,
            cost_of,
        )
        found = {f["session"]: f["max_tokens"] for f in context.local(conn, now)}
    assert found == {"b": 1_000_000}


def test_drift_ignores_verified_community_errors():
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    from pricing_drift import drift

    payload = {
        "claude-mythos-preview": {
            "litellm_provider": "anthropic",
            "input_cost_per_token": 1e-05,
            "output_cost_per_token": 5e-05,
        }
    }
    assert drift(payload, pricing.BUILTIN) == []
