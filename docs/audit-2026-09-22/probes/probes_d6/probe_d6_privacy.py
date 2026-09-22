"""d6 probes: prompt text retention and AI summary payload."""
from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient

from vibewatt import config as configmod
from vibewatt import store
from vibewatt.api import create_app


def _jsonl(tmp_path, recs):
    path = tmp_path / "claude" / "projects" / "secretproj" / "s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def _turn(ts, rid):
    return {"type": "assistant", "requestId": rid, "timestamp": ts, "sessionId": "s1",
            "cwd": "/work/secretproj",
            "message": {"id": rid, "model": "claude-opus-5",
                        "usage": {"input_tokens": 10, "output_tokens": 5}}}


def _client(**extra):
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    cfg.update(extra)
    return TestClient(create_app(cfg))


def test_every_distinct_last_prompt_is_retained(tmp_path):
    recs = [_turn("2026-09-15T01:00:00Z", "a")]
    for i, text in enumerate(["PRIVATE prompt one", "PRIVATE prompt two", "PRIVATE prompt three"]):
        recs.append({"type": "last-prompt", "sessionId": "s1",
                     "timestamp": f"2026-09-15T0{i + 2}:00:00Z", "lastPrompt": text})
    recs.append({"type": "user", "sessionId": "s1", "timestamp": "2026-09-15T00:59:00Z",
                 "message": {"role": "user", "content": "PRIVATE first user msg"}})
    _jsonl(tmp_path, recs)
    client = _client()
    client.post("/api/sync")
    with store.connect() as conn:
        rows = [r["text"] for r in conn.execute("SELECT text FROM prompts WHERE session='s1'")]
    title = client.get("/api/sessions").json()[0]["title"]
    print("stored prompt rows:", rows, "| displayed title:", title)
    assert len(rows) == 3  # three prompts retained, one is shown


def test_weekly_payload_has_aggregates_only(tmp_path, monkeypatch):
    from vibewatt import weekly

    _jsonl(tmp_path, [_turn("2026-09-20T01:00:00Z", "a"),
                      {"type": "last-prompt", "sessionId": "s1", "timestamp": "2026-09-20T02:00:00Z",
                       "lastPrompt": "PRIVATE prompt text"}])
    bodies = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        bodies.append(req.data.decode())
        return Resp(json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode())

    monkeypatch.setattr(weekly, "urlopen", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client = _client(ai_summary={"enabled": True})
    client.post("/api/sync")
    r = client.post("/api/weekly-summary")
    print(r.json()["status"], bodies[0][:300] if bodies else None)
    assert bodies and "PRIVATE" not in bodies[0] and "secretproj" not in bodies[0]
    client2 = _client(ai_summary={"enabled": False})
    bodies.clear()
    client2.post("/api/weekly-summary")
    assert bodies == []
