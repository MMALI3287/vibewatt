from __future__ import annotations

import json
from pathlib import Path

from ccburn.ingest import discover, parse_file
from ccburn.sources import load


def test_ingest_discover_finds_local_logs(tmp_path, monkeypatch):
    claude_dir = tmp_path / ".claude" / "projects" / "demo"
    claude_dir.mkdir(parents=True)
    claude_file = claude_dir / "session.jsonl"
    claude_file.write_text('{"type":"assistant","timestamp":"2026-09-15T01:00:00Z","requestId":"r1","sessionId":"s1","cwd":"/tmp/demo","message":{"id":"m1","model":"claude-opus-5","usage":{"input_tokens":10,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":5,"output_tokens_details":{"thinking_tokens":1},"server_tool_use":{"web_search_requests":0},"speed":"standard","inference_geo":"global"}}}\n', encoding="utf-8")

    cowork_root = tmp_path / "Claude" / "local-agent-mode-sessions" / "acct" / "space"
    cowork_root.mkdir(parents=True)
    cowork_file = cowork_root / "audit.jsonl"
    cowork_file.write_text('{"type":"assistant","timestamp":"2026-09-15T02:00:00Z","requestId":"r2","session_id":"s2","message":{"id":"m2","model":"claude-opus-5","usage":{"input_tokens":20,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":10,"output_tokens_details":{"thinking_tokens":2},"server_tool_use":{"web_search_requests":0},"speed":"standard","inference_geo":"global"}}}\n', encoding="utf-8")

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("APPDATA", str(tmp_path))

    found = discover()
    values = {(source, str(path)) for source, path in found}
    assert ("claude-code", str(claude_file)) in values
    assert ("cowork", str(cowork_file)) in values


def test_parse_file_and_dedupe(tmp_path):
    path = tmp_path / "file.jsonl"
    payload = {
        "type": "assistant",
        "timestamp": "2026-09-15T01:00:00Z",
        "requestId": "req-1",
        "sessionId": "s1",
        "cwd": "/tmp/demo",
        "message": {
            "id": "msg-1",
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": 11,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 7,
                "output_tokens_details": {"thinking_tokens": 2},
                "server_tool_use": {"web_search_requests": 0},
                "speed": "standard",
                "inference_geo": "global",
            },
        },
    }
    path.write_text(json.dumps(payload) + "\n" + json.dumps(payload) + "\n", encoding="utf-8")

    turns, duplicates = load([("claude-code", path)])
    assert len(turns) == 1
    assert duplicates == 1
    assert turns[0].session == "s1"
    assert turns[0].input == 11

    parsed = parse_file("claude-code", path)
    assert len(parsed) == 1
    assert parsed[0].key == ("msg-1", "req-1")
