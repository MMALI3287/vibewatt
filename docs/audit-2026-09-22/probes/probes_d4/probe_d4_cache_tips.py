"""d4 probes: 7.6 cache scanner and 7.7 tips."""

from __future__ import annotations

import pytest

from vibewatt.analysis import cache_scan, tips, waste
from vibewatt.pricing import rate_for


def turn(**changes):
    row = {
        "msg_id": "m", "request_id": "r", "ts": "2026-09-01T00:00:00+00:00",
        "day": "2026-09-01", "source": "claude-code", "project": "demo", "session": "s",
        "model": "claude-opus-5", "input": 0, "cache_5m": 0, "cache_1h": 0,
        "cache_read": 0, "output": 100, "thinking": 0, "web_search": 0, "sidechain": 0,
        "fast": 0, "geo": None, "cost": 1.0,
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("model", ["claude-opus-5", "claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"])
def test_spec_fixture_zero_hit_1m_input_equals_spec_formula(model):
    r = rate_for(model)
    got = cache_scan.detect("s", [turn(model=model, input=1_000_000)], None)
    spec = 1_000_000 * (r.input - r.cache_read) / 1_000_000
    print(model, "stated", got[0].savings_usd, "spec", spec, "hit", got[0].metrics["hit_rate"])
    assert got[0].savings_usd == pytest.approx(spec)


def test_cache_writes_are_converted_beyond_spec_formula():
    # 300k uncached input, 700k 1h cache writes, 0 reads: hit 0%, total 1M.
    r = rate_for("claude-opus-5")
    got = cache_scan.detect("s", [turn(input=300_000, cache_1h=700_000)], None)[0]
    spec = 300_000 * (r.input - r.cache_read) / 1e6
    print("stated", got.savings_usd, "spec input-only", spec)
    assert got.savings_usd > spec


def test_cache_flags_hit_rate_just_below_half_and_total_just_above_200k():
    # hit = 100_000/200_001 < 0.5, total 200_001 > 200k
    got = cache_scan.detect("s", [turn(input=100_001, cache_read=100_000)], None)
    assert got
    # total exactly 200k with 0% hit -> no finding (spec: exceeds 200k)
    assert not cache_scan.detect("s", [turn(input=200_000)], None)


def test_output_only_tokens_do_not_count_as_input():
    assert not cache_scan.detect("s", [turn(input=150_000, output=900_000)], None)


def series(n=5, **changes):
    return [turn(msg_id=f"m{i}", ts=f"2026-09-01T0{i}:00:00+00:00", **changes) for i in range(n)]


def test_model_mix_trigger_nontrigger_and_boundaries():
    trig = tips.detect("s", series(output=499), [], None)
    assert "model_mix" in {t.rule for t in trig}
    assert "model_mix" not in {t.rule for t in tips.detect("s", series(output=500), [], None)}
    assert "model_mix" not in {t.rule for t in tips.detect("s", series(n=4, output=10), [], None)}
    # Sonnet sessions never trigger the Opus rule
    assert "model_mix" not in {
        t.rule for t in tips.detect("s", series(model="claude-sonnet-4-5", output=10), [], None)
    }
    # Bedrock/Vertex style ids still count as Opus
    assert "model_mix" in {
        t.rule for t in tips.detect("s", series(model="us.anthropic.claude-opus-4-1-20250805-v1:0", output=10), [], None)
    }
    print("model_mix savings:", [t.savings_usd for t in trig if t.rule == "model_mix"])


def test_fast_mode_on_model_without_fast_table_gives_no_tip():
    got = tips.detect("s", series(fast=1, model="claude-sonnet-4-5", input=1000), [], None)
    print("fast sonnet tips:", [(t.rule, t.savings_usd) for t in got])
    assert "fast_mode" not in {t.rule for t in got}


def test_subagent_tip_trigger_and_nontrigger():
    rows = series(sidechain=1, input=1000)
    ev = waste.detect("s", rows, [])
    assert "tip_subagent_heavy" in {t.rule for t in tips.detect("s", rows, ev, None)}
    rows2 = [dict(r, sidechain=1 if i < 2 else 0) for i, r in enumerate(series(input=1000))]
    ev2 = waste.detect("s", rows2, [])
    assert "tip_subagent_heavy" not in {t.rule for t in tips.detect("s", rows2, ev2, None)}
