from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ccburn.aggregate import build, cost_of
from ccburn.pricing import MILLION, rate_for
from ccburn.sources import Turn, read_file


def mk(model="claude-opus-5", **kw):
    base = dict(source="claude-code", ts=datetime(2026, 9, 1, tzinfo=timezone.utc),
                model=model, input=0, cache_5m=0, cache_1h=0, cache_read=0, output=0,
                thinking=0, web_searches=0, fast=False, geo=None, sidechain=False,
                project="p", session="s", key=("m", "r"))
    base.update(kw)
    return Turn(**base)


def test_cost_of_ttl_split_and_read_rate():
    r = rate_for("claude-opus-5")
    assert cost_of(mk(cache_1h=MILLION)) == pytest.approx(r.input * 2.0)
    assert cost_of(mk(cache_5m=MILLION)) == pytest.approx(r.input * 1.25)
    assert cost_of(mk(cache_read=MILLION)) == pytest.approx(r.input * 0.1)


def test_unknown_model_unpriced():
    t = mk(model="totally-unknown-x", input=1000)
    assert cost_of(t) is None
    rep = build([t], tz=timezone.utc)
    assert "totally-unknown-x" in rep.unknown_models
    assert rep.total.unpriced == 1


def line(usage):
    return json.dumps({"type": "assistant", "timestamp": "2026-09-01T00:00:00Z",
                       "requestId": "r1", "sessionId": "s",
                       "message": {"id": "m1", "model": "claude-opus-5", "usage": usage}})


@pytest.mark.parametrize("usage,exp", [
    ({"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 300,
      "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200}}, (100, 200)),
    ({"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 300,
      "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}, (300, 0)),
    ({"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 300}, (300, 0)),
])
def test_parser_ttl_split(tmp_path, usage, exp):
    p = tmp_path / "a.jsonl"
    p.write_text(line(usage) + "\n", encoding="utf-8")
    turns = list(read_file("claude-code", p))
    assert len(turns) == 1
    assert (turns[0].cache_5m, turns[0].cache_1h) == exp
