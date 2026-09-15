from __future__ import annotations

import json
from datetime import datetime, timezone

from ccburn import quota
from ccburn import store


def test_store_schema_includes_files_and_quota_samples(tmp_path):
    path = tmp_path / "ccburn.db"
    with store.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }

    assert "files" in tables
    assert "quota_samples" in tables
    assert "meta" in tables


def test_store_sync_keeps_unchanged_turn_count(tmp_path):
    path = tmp_path / "ccburn.db"
    log = tmp_path / "session.jsonl"
    log.write_text(
        '{"type":"assistant","timestamp":"2026-09-15T01:00:00Z","requestId":"r1","sessionId":"s1","cwd":"/tmp/demo","message":{"id":"m1","model":"claude-opus-5","usage":{"input_tokens":15,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":5,"output_tokens_details":{"thinking_tokens":1},"server_tool_use":{"web_search_requests":0},"speed":"standard","inference_geo":"global"}}}\n',
        encoding="utf-8",
    )

    with store.connect(path) as conn:
        turns = store.sync_directory(conn, [("claude-code", log)], None)
        first = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        rows_before = conn.execute("SELECT turn_count FROM files WHERE path = ?", (str(log),)).fetchone()[0]
        second = store.sync_directory(conn, [("claude-code", log)], None)
        rows_after = conn.execute("SELECT turn_count FROM files WHERE path = ?", (str(log),)).fetchone()[0]

    assert turns == 1
    assert first == 1
    assert second == 0
    assert rows_before == rows_after == 1


def test_quota_read_persists_samples(tmp_path, monkeypatch):
    path = tmp_path / "ccburn.db"
    monkeypatch.setenv("CCBURN_DATA_DIR", str(tmp_path))

    class DummyQuota:
        windows = [
            quota.Window(label="5-hour", utilization=60.0, resets_at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)),
            quota.Window(label="7-day", utilization=40.0, resets_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)),
        ]
        source = "endpoint"
        fetched_at = datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(quota, "fetch", lambda token=None, timeout=10.0: DummyQuota())
    monkeypatch.setattr(quota, "read_token", lambda: "token")

    q, note = quota.read({"quota": True})
    assert q is not None
    assert note == ""
    with store.connect(path) as conn:
        samples = conn.execute("SELECT label, utilization FROM quota_samples ORDER BY label").fetchall()
    assert {row[0]: row[1] for row in samples} == {"5-hour": 60.0, "7-day": 40.0}
