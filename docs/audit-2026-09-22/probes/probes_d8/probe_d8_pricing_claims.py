"""d8 probes: README/CLAUDE.md pricing claims that no suite test pins directly."""
from __future__ import annotations

import json

import pytest

from vibewatt.aggregate import cost_of
from vibewatt.ingest import claude_code


def _line(model, usage):
    return {"type": "assistant", "requestId": "r1", "timestamp": "2026-09-15T00:00:00Z",
            "sessionId": "s", "cwd": "/w/p", "message": {"id": "m1", "model": model, "usage": usage}}


def _turn(tmp_path, model, usage):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(_line(model, usage)) + "\n", encoding="utf-8")
    return list(claude_code.parse(p))[0]


def test_d8_cache_ttl_split_prices_1h_at_2x_and_5m_at_1_25x(tmp_path):
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 2_000_000,
             "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000,
                                "ephemeral_5m_input_tokens": 1_000_000}}
    t = _turn(tmp_path, "claude-opus-5", usage)
    # opus-5 base input $5/M: 1h -> $10, 5m -> $6.25
    assert cost_of(t) == pytest.approx(16.25)


def test_d8_unknown_model_is_unpriced_not_zero(tmp_path):
    t = _turn(tmp_path, "claude-imaginary-9", {"input_tokens": 1000, "output_tokens": 1000})
    assert cost_of(t) is None
