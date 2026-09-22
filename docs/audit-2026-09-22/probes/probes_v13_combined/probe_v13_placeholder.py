from __future__ import annotations
import json
from datetime import timezone
from pathlib import Path
from vibewatt import sources, store
from vibewatt.aggregate import cost_of
SRC = "claude-code"


def _line(out, stop, block):
    return json.dumps({"type": "assistant", "requestId": "rX", "timestamp": "2026-09-15T20:00:00Z",
        "sessionId": "s1", "cwd": "/w/demo", "message": {"id": "mX", "model": "claude-opus-5",
        "stop_reason": stop, "content": [{"type": block}],
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "output_tokens": out}}})


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "proj" / "s1.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(_line(8, None, "thinking") + "\n" + _line(612, "end_turn", "text") + "\n", encoding="utf-8")
    return p


def test_load_keeps_final_usage(tmp_path):
    p = _write(tmp_path)
    turns, dups = sources.load([("claude_code", p)])
    assert len(turns) == 1 and dups == 1
    assert turns[0].output == 612, f"load kept output={turns[0].output}"


def test_sync_keeps_final_usage(tmp_path):
    p = _write(tmp_path)
    with store.connect(tmp_path / "s.db") as conn:
        store.sync_files(conn, [(SRC, p)], timezone.utc, cost_of)
        rows = conn.execute("SELECT output FROM turns").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 612, f"store kept output={rows[0][0]}"
