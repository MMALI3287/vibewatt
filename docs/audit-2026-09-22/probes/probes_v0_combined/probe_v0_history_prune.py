"""v0-combined probe: history rollup under partial pruning of a day."""

from __future__ import annotations

import json
from datetime import date, timedelta, timezone

from fastapi.testclient import TestClient

JST = timezone(timedelta(hours=9))


def _rec(mid, ts, inp, model="claude-opus-5", session="s"):
    return {"type": "assistant", "timestamp": ts, "sessionId": session, "requestId": "r" + mid,
            "cwd": "/w", "message": {"id": mid, "model": model, "usage": {
                "input_tokens": inp, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0, "output_tokens": 0}}}


def _write(p, recs):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return p


def _cfg():
    return {"offline": True, "quota": False, "history": True}


def test_same_model_partial_prune(tmp_path):
    from vibewatt.cli import build_report
    root = tmp_path / "claude" / "projects" / "p"
    old = _write(root / "old.jsonl", [_rec("a", "2026-08-01T01:00:00Z", 5000)])
    _write(root / "new.jsonl", [_rec("b", "2026-08-01T14:30:00Z", 100),
                                _rec("c", "2026-08-01T15:30:00Z", 100)])
    r1, *_ = build_report(_cfg(), JST)
    old.unlink()
    r2, *_ = build_report(_cfg(), JST)
    r3, *_ = build_report(_cfg(), JST)  # does the loss persist?
    stored = json.loads((tmp_path / "data" / "history.json").read_text())["days"]
    d = date(2026, 8, 1)
    print("\nSAME-MODEL Aug1 input", r1.by_day[d].input, "->", r2.by_day[d].input, "->",
          r3.by_day[d].input, "| total", r1.total.input, "->", r3.total.input,
          "| stored", stored["2026-08-01|claude-opus-5"]["input"])
    assert r3.total.input == r1.total.input


def test_other_model_partial_prune(tmp_path):
    from vibewatt.cli import build_report
    root = tmp_path / "claude" / "projects" / "p"
    old = _write(root / "old.jsonl", [_rec("a", "2026-08-01T01:00:00Z", 5000,
                                           model="claude-sonnet-4-5")])
    _write(root / "new.jsonl", [_rec("b", "2026-08-01T14:30:00Z", 100)])
    r1, *_ = build_report(_cfg(), JST)
    old.unlink()
    r2, *_ = build_report(_cfg(), JST)
    stored = json.loads((tmp_path / "data" / "history.json").read_text())["days"]
    print("\nOTHER-MODEL total", r1.total.input, "->", r2.total.input,
          "| stored keys", sorted(stored), "| models in report", sorted(r2.by_model))
    assert r2.total.input == r1.total.input


def test_api_summary_reflects_loss(tmp_path):
    from vibewatt import config as configmod
    from vibewatt.api import create_app
    root = tmp_path / "claude" / "projects" / "p"
    old = _write(root / "old.jsonl", [_rec("a", "2026-08-01T01:00:00Z", 5000)])
    _write(root / "new.jsonl", [_rec("b", "2026-08-01T14:30:00Z", 100)])
    cfg = configmod.load()
    cfg.update(_cfg())
    c = TestClient(create_app(cfg))
    before = c.get("/api/summary").json()
    old.unlink()
    after = c.get("/api/summary").json()
    daily = c.get("/api/daily").json()
    print("\nAPI summary total keys", list(before)[:8])
    print("API before", json.dumps(before.get("total"))[:200])
    print("API after ", json.dumps(after.get("total"))[:200], "daily", daily)
    assert before["total"] == after["total"]
