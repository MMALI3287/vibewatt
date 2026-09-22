"""d7 probe: Claude Code streams a placeholder usage line before the final one.

Shape mirrors 406 real responses (aggregated, no content copied): same
(message.id, requestId) twice in one file, first line output_tokens small with
stop_reason null, last line the final count with stop_reason set.
"""
from __future__ import annotations

import json
from pathlib import Path

from ccburn.sources import CLAUDE_CODE, load


def _line(out: int, stop: str | None) -> str:
    return json.dumps({
        "type": "assistant", "requestId": "req_1", "timestamp": "2026-09-15T10:00:00Z",
        "sessionId": "s1", "cwd": "/w/demo",
        "message": {"id": "msg_1", "model": "claude-opus-5", "stop_reason": stop,
                    "usage": {"input_tokens": 3, "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 100, "output_tokens": out}},
    })


def test_final_usage_line_wins(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    f.write_text(_line(8, None) + "\n" + _line(612, "end_turn") + "\n", encoding="utf-8")
    turns, dups = load([(CLAUDE_CODE, f)])
    assert len(turns) == 1 and dups == 1
    assert turns[0].output == 612, f"kept streaming placeholder output={turns[0].output}"
