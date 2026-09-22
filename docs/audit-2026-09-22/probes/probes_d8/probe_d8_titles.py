"""d8 probes: PLAN 7.2 title derivation (last-prompt, first-user fallback, summary preference)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ccburn import config as configmod
from ccburn.api import create_app


def _client():
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    return TestClient(create_app(cfg))


def _asst(sid, mid, ts):
    return {"type": "assistant", "requestId": "r" + mid, "timestamp": ts, "sessionId": sid,
            "cwd": "/work/demo", "message": {"id": mid, "model": "claude-opus-5",
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens": 5}}}


def _user(sid, text, ts):
    return {"type": "user", "timestamp": ts, "sessionId": sid,
            "message": {"role": "user", "content": text}}


def _write(tmp_path, name, recs):
    p = tmp_path / "claude" / "projects" / "demo" / f"{name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _title(client, sid):
    return client.get(f"/api/sessions/{sid}").json()["title"]


def test_d8_first_user_message_fallback(tmp_path):
    _write(tmp_path, "nolast", [
        _user("nolast", "refactor the parser please", "2026-09-15T01:00:00Z"),
        _asst("nolast", "a1", "2026-09-15T01:00:05Z"),
    ])
    c = _client()
    c.post("/api/sync")
    assert _title(c, "nolast") == "refactor the parser please"


def test_d8_last_prompt_wins(tmp_path):
    _write(tmp_path, "withlast", [
        _user("withlast", "first message", "2026-09-15T01:00:00Z"),
        _asst("withlast", "b1", "2026-09-15T01:00:05Z"),
        {"type": "last-prompt", "sessionId": "withlast", "timestamp": "2026-09-15T01:05:00Z",
         "lastPrompt": "the last prompt"},
    ])
    c = _client()
    c.post("/api/sync")
    assert _title(c, "withlast") == "the last prompt"


def test_d8_summary_record_preferred(tmp_path):
    # PLAN 7.2: "prefer `summary` when present"
    _write(tmp_path, "withsummary", [
        {"type": "summary", "summary": "Fix Windows build", "leafUuid": "u1"},
        _user("withsummary", "first message", "2026-09-15T01:00:00Z"),
        _asst("withsummary", "c1", "2026-09-15T01:00:05Z"),
        {"type": "last-prompt", "sessionId": "withsummary", "timestamp": "2026-09-15T01:05:00Z",
         "lastPrompt": "the last prompt"},
    ])
    c = _client()
    c.post("/api/sync")
    assert _title(c, "withsummary") == "Fix Windows build"
