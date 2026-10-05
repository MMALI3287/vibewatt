"""Phase 10: OpenAI rates for models seen in Codex logs, each with a cited source."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from vibewatt import pricing
from vibewatt.aggregate import cost_of
from vibewatt.sources import Turn

ONE_M = 1_000_000
# Under the 272K long-context threshold, so the base rate applies.
TOKENS = 200_000


def turn(model: str, day: str = "2026-10-03", **counts) -> Turn:
    base = {"input": 0, "cache_5m": 0, "cache_1h": 0, "cache_read": 0, "output": 0}
    base.update(counts)
    return Turn(
        "codex",
        datetime.fromisoformat(day).replace(tzinfo=UTC),
        model,
        base["input"],
        base["cache_5m"],
        base["cache_1h"],
        base["cache_read"],
        base["output"],
        0,
        0,
        False,
        None,
        False,
        "demo",
        "s",
        ("k", "r"),
    )


@pytest.mark.parametrize(
    "model,day,rate",
    [
        # developers.openai.com/api/docs/models/<id>, retrieved 2026-10-05
        ("gpt-6-astra", "2026-10-03", pricing.Rate(10, 12.5, 12.5, 1.0, 50)),
        ("gpt-6.1-sol", "2026-10-03", pricing.Rate(2, 2.5, 2.5, 0.10, 10)),
        ("gpt-5.5", "2026-06-01", pricing.Rate(5, 5, 5, 0.50, 30)),
        # promotional price from the 2026-08-21 changelog entry
        ("gpt-5.6-sol", "2026-10-02", pricing.Rate(4, 5, 5, 0.40, 20)),
    ],
)
def test_openai_models_are_priced_per_field(monkeypatch, model, day, rate):
    monkeypatch.setattr(pricing, "_remote", {})
    for field, expected in zip(
        ("input", "cache_5m", "cache_1h", "cache_read", "output"), rate, strict=True
    ):
        assert cost_of(turn(model, day, **{field: TOKENS})) == pytest.approx(
            expected * TOKENS / ONE_M
        )


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol", "gpt-5.5"])
def test_prompt_over_272k_reprices_the_whole_request(model):
    base = pricing.rate_for(model, ts=turn(model).ts)
    rate = pricing.rate_for(model, ts=turn(model).ts, prompt_tokens=272_001)
    assert rate.input == base.input * 2 and rate.cache_read == base.cache_read * 2
    assert rate.output == base.output * 1.5
    assert pricing.rate_for(model, ts=turn(model).ts, prompt_tokens=272_000) == base


def test_the_272k_boundary_is_applied_through_cost_of():
    prompt = turn("gpt-6-astra", input=272_001)
    assert cost_of(prompt) == pytest.approx(272_001 * 20 / ONE_M)
    assert cost_of(replace(prompt, input=272_000)) == pytest.approx(
        272_000 * 10 / ONE_M
    )


@pytest.mark.parametrize("day", ["2026-08-20", "2026-11-22"])
def test_sol_promotion_is_only_priced_inside_its_verified_window(day):
    assert cost_of(turn("gpt-5.6-sol", day, input=ONE_M)) is None


@pytest.mark.parametrize("model", ["gpt-9-unknown", "codex-future-helper"])
def test_models_without_a_cited_rate_are_unpriced_never_zero(model):
    assert cost_of(turn(model, input=ONE_M)) is None


def test_gpt_5_5_is_not_priced_before_its_release():
    assert cost_of(turn("gpt-5.5", "2026-04-23", input=ONE_M)) is None
    assert cost_of(turn("gpt-5.5", "2026-04-24", input=TOKENS)) == pytest.approx(1.0)


def test_a_large_prompt_with_a_known_threshold_has_no_unknown_premium():
    assert not pricing.context_premium_unknown(turn("gpt-6-astra", input=500_000))


def test_a_large_sol_prompt_is_flagged_because_its_threshold_is_unverified():
    assert pricing.context_premium_unknown(turn("gpt-5.6-sol", input=300_000))
    assert not pricing.context_premium_unknown(turn("gpt-5.6-sol", input=100_000))


@pytest.mark.parametrize(
    "model,day,rate",
    [
        # developers.openai.com/api/docs/models/gpt-5.4; released 2026-03-05
        ("gpt-5.4", "2026-05-04", pricing.Rate(2.5, 2.5, 2.5, 0.25, 15)),
        # models/gpt-5.6-terra: price after the 2026-07-30 cut
        ("gpt-5.6-terra", "2026-09-17", pricing.Rate(2, 2.5, 2.5, 0.20, 12)),
        # alignment.openai.com/auto-review (2026-04-30): GPT-5.4 Thinking, low
        ("codex-auto-review", "2026-05-01", pricing.Rate(2.5, 2.5, 2.5, 0.25, 15)),
    ],
)
def test_models_found_unpriced_now_have_cited_rates(monkeypatch, model, day, rate):
    monkeypatch.setattr(pricing, "_remote", {})
    for field, expected in zip(
        ("input", "cache_5m", "cache_1h", "cache_read", "output"), rate, strict=True
    ):
        assert cost_of(turn(model, day, **{field: TOKENS})) == pytest.approx(
            expected * TOKENS / ONE_M
        )


@pytest.mark.parametrize(
    "model,day",
    [
        ("gpt-5.4", "2026-03-04"),
        ("gpt-5.6-terra", "2026-07-29"),  # before the cut the cache rates are unlisted
        ("codex-auto-review", "2026-04-29"),  # before OpenAI named its model
    ],
)
def test_new_rates_start_on_their_evidence_date(model, day):
    assert cost_of(turn(model, day, input=TOKENS)) is None


@pytest.mark.parametrize("model", ["gpt-5.4", "gpt-5.6-terra", "codex-auto-review"])
def test_new_models_follow_the_272k_rule(model):
    ts = turn(model, "2026-09-17").ts
    base = pricing.rate_for(model, ts=ts)
    assert pricing.rate_for(model, ts=ts, prompt_tokens=272_001).input == base.input * 2
