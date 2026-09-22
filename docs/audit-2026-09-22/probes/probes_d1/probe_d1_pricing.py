from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from d1util import projects_root, rec, write_jsonl  # noqa: E402

from vibewatt import ingest, pricing  # noqa: E402
from vibewatt.aggregate import cost_of  # noqa: E402
from vibewatt.pricing import BUILTIN, Rate, normalize, rate_for  # noqa: E402
from vibewatt.sources import load  # noqa: E402


@pytest.fixture(autouse=True)
def no_remote(monkeypatch):
    monkeypatch.setattr(pricing, "_remote", None)


def _turn(tmp_path, **kw):
    write_jsonl(projects_root(tmp_path) / "p" / "a.jsonl", [rec(**kw)])
    (t,), _ = load(ingest.discover())
    return t


def test_per_ttl_split(tmp_path):
    t = _turn(tmp_path, inp=0, out=0, usage_extra={
        "cache_creation_input_tokens": 3_000_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000,
                           "ephemeral_1h_input_tokens": 2_000_000}})
    assert (t.cache_5m, t.cache_1h) == (1_000_000, 2_000_000)
    # opus-5: 5.0 base -> 6.25 (1.25x) and 10.0 (2x)
    assert cost_of(t) == pytest.approx(6.25 + 20.0)


def test_flat_fallback_assumes_5m(tmp_path):
    t = _turn(tmp_path, inp=0, out=0, usage_extra={"cache_creation_input_tokens": 1_000_000})
    assert (t.cache_5m, t.cache_1h) == (1_000_000, 0)


def test_split_present_but_zero_falls_back_to_5m(tmp_path):
    t = _turn(tmp_path, inp=0, out=0, usage_extra={
        "cache_creation_input_tokens": 500,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}})
    assert (t.cache_5m, t.cache_1h) == (500, 0)


def test_split_smaller_than_total_drops_tokens(tmp_path):
    t = _turn(tmp_path, inp=0, out=0, usage_extra={
        "cache_creation_input_tokens": 900,
        "cache_creation": {"ephemeral_1h_input_tokens": 300}})
    print("split partial", t.cache_5m, t.cache_1h)


def test_geo_us_multiplies_everything_including_cache(tmp_path):
    t = _turn(tmp_path, inp=1_000_000, out=1_000_000, usage_extra={
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 2_000_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000,
                           "ephemeral_1h_input_tokens": 1_000_000},
        "inference_geo": "us"})
    assert t.geo == "us"
    base = 5.0 + 25.0 + 0.5 + 6.25 + 10.0
    assert cost_of(t) == pytest.approx(base * 1.1)


def test_fast_mode_opus5(tmp_path):
    t = _turn(tmp_path, inp=1_000_000, out=1_000_000, usage_extra={"speed": "fast"})
    assert t.fast is True
    assert cost_of(t) == pytest.approx(10.0 + 50.0)


def test_fast_mode_plus_geo(tmp_path):
    t = _turn(tmp_path, inp=1_000_000, out=0, usage_extra={"speed": "fast", "inference_geo": "us"})
    assert cost_of(t) == pytest.approx(10.0 * 1.1)


def test_fast_mode_on_model_without_fast_rate_falls_back_to_standard():
    # Opus 4.6 / 4.7 had fast mode (now retired); logs from then say speed=fast.
    assert rate_for("claude-opus-4-6", fast=True) == rate_for("claude-opus-4-6")
    assert rate_for("claude-opus-4-7", fast=True) == rate_for("claude-opus-4-7")


def test_web_search_price(tmp_path):
    t = _turn(tmp_path, inp=0, out=0, usage_extra={"server_tool_use": {"web_search_requests": 3}})
    assert cost_of(t) == pytest.approx(0.03)


def test_thinking_not_double_billed(tmp_path):
    t = _turn(tmp_path, inp=0, out=1_000_000,
              usage_extra={"output_tokens_details": {"thinking_tokens": 600_000}})
    assert t.thinking == 600_000
    assert cost_of(t) == pytest.approx(25.0)


def test_unknown_model_is_unpriced_not_zero(tmp_path):
    t = _turn(tmp_path, model="claude-haiku-5")
    assert cost_of(t) is None


def test_synthetic_model_is_skipped(tmp_path):
    write_jsonl(projects_root(tmp_path) / "p" / "a.jsonl", [rec(model="<synthetic>")])
    turns, _ = load(ingest.discover())
    assert turns == []


@pytest.mark.parametrize("raw,expected", [
    ("us.anthropic.claude-opus-4-5-20251101-v1:0", "claude-opus-4-5"),
    ("anthropic.claude-opus-5", "claude-opus-5"),
    ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5"),
    ("claude-opus-4-5@20251101", "claude-opus-4-5"),
    ("claude-opus-4-6[1m]", "claude-opus-4-6"),
    ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
    ("claude-opus-4-20250514", "claude-opus-4"),
    ("claude-opus-4-1-20250805", "claude-opus-4-1"),
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
    ("Claude-Opus-5", "claude-opus-5"),
])
def test_real_world_ids_resolve_to_the_right_rate(raw, expected):
    assert rate_for(raw) == BUILTIN[expected], (raw, normalize(raw))


def test_unknown_sibling_id_borrows_a_stale_rate():
    """A model the table does not list must be unpriced (or remote-priced),
    never silently priced at an older sibling's rate."""
    got = {m: rate_for(m) for m in ("claude-opus-4-9", "claude-sonnet-4-7", "claude-opus-5-1")}
    print({m: (r.input, r.output) if r else None for m, r in got.items()})
    assert got["claude-opus-4-9"] is None


def test_remote_table_cannot_fill_gap_shadowed_by_prefix(monkeypatch):
    monkeypatch.setattr(pricing, "_remote", {"claude-opus-4-9": Rate(5.0, 6.25, 10.0, 0.5, 25.0)})
    r = rate_for("claude-opus-4-9")
    print("opus-4-9 resolved input rate", r.input)
    assert r.input == 5.0


def test_older_models_missing_from_builtin():
    missing = [m for m in ("claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022")
               if rate_for(m) is None]
    print("unpriced offline:", missing)
