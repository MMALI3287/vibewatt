"""d10 probes: malformed-input robustness of sync. Synthetic data only."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import timezone
from pathlib import Path

import pytest

from ccburn import store
from ccburn.aggregate import cost_of
from ccburn.ingest import discover


def line(i, sess="s1", mid=None, rid=None, usage=None, ts="2026-09-15T20:00:00Z", **extra):
    rec = {
        "type": "assistant", "requestId": rid or f"r{i}", "timestamp": ts,
        "sessionId": sess, "cwd": "/work/demo",
        "message": {"id": mid or f"m{i}", "model": "claude-opus-5",
                    "usage": usage or {"input_tokens": 10, "output_tokens": 5}},
    }
    rec.update(extra)
    return json.dumps(rec)


def proj(tmp_path) -> Path:
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def do_sync():
    with store.connect() as conn:
        res = store.sync_files(conn, discover({}), timezone.utc, lambda t: cost_of(t, None))
        n = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        keys = {r[0] for r in conn.execute("SELECT msg_id FROM turns")}
    return res, n, keys


def test_corrupt_lines_skipped(tmp_path):
    d = proj(tmp_path)
    data = (line(1) + "\n" + line(2)[:40] + "\n" + "garbage{{{\n").encode()
    data += b'{"type":"assistant","x":"\xff\xfe\xc3"}\n' + line(3).encode() + b"\n"
    (d / "a.jsonl").write_bytes(data)
    _, n, keys = do_sync()
    assert keys == {"m1", "m3"}


def test_bom_first_line_counted(tmp_path):
    d = proj(tmp_path)
    (d / "bom.jsonl").write_bytes(b"\xef\xbb\xbf" + (line(1) + "\n" + line(2) + "\n").encode())
    _, n, keys = do_sync()
    assert keys == {"m1", "m2"}, keys


@pytest.mark.parametrize("bad", [
    line(9, usage={"input_tokens": "abc", "output_tokens": 5}),
    line(9, usage={"input_tokens": {"x": 1}, "output_tokens": 5}),
    line(9, usage={"input_tokens": 1, "output_tokens": 5, "output_tokens_details": [1]}),
    line(9, usage={"input_tokens": 1, "output_tokens": 5, "server_tool_use": "x"}),
    line(9, ts=12345),
    json.dumps({"type": "assistant", "requestId": "r9", "timestamp": "2026-09-15T20:00:00Z",
                "sessionId": "s1", "message": {"id": "m9", "model": {"n": 1},
                                               "usage": {"input_tokens": 1}}}),
    '["user"]',
    json.dumps({"type": "last-prompt", "sessionId": "s1", "lastPrompt": 5}),
    json.dumps({"type": "user", "sessionId": "s1", "message": "hi"}),
    json.dumps({"type": "user", "sessionId": "s1",
                "message": {"content": [{"type": "text", "text": 7}]}}),
], ids=["str-int", "dict-int", "details-list", "server-str", "ts-int", "model-dict",
        "list-user", "lastprompt-int", "user-msg-str", "user-text-int"])
def test_bad_value_types_do_not_crash_or_drop_other_files(tmp_path, bad):
    d = proj(tmp_path)
    (d / "good.jsonl").write_text(line(1) + "\n" + line(2) + "\n")
    (d / "zbad.jsonl").write_text(line(3) + "\n" + bad + "\n")
    _, n, keys = do_sync()
    assert {"m1", "m2", "m3"} <= keys


def test_empty_file_and_dir_named_jsonl(tmp_path):
    d = proj(tmp_path)
    (d / "empty.jsonl").write_text("")
    (d / "dir.jsonl").mkdir()
    (d / "dir.jsonl" / "inner.jsonl").write_text(line(5) + "\n")
    (d / "good.jsonl").write_text(line(1) + "\n")
    res, n, keys = do_sync()
    assert keys == {"m1", "m5"}


def _lock_exclusive(path: Path):
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    h = k32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, share 0
    assert h not in (None, ctypes.c_void_p(-1).value)
    return h


@pytest.mark.skipif(sys.platform != "win32", reason="windows share modes")
def test_locked_file_is_retried_next_sync(tmp_path):
    d = proj(tmp_path)
    (d / "good.jsonl").write_text(line(1) + "\n")
    locked = d / "locked.jsonl"
    locked.write_text(line(2) + "\n" + line(3) + "\n")
    h = _lock_exclusive(locked)
    try:
        res1, n1, keys1 = do_sync()
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
    # the lock is gone and the file is unchanged: its turns must arrive now
    res2, n2, keys2 = do_sync()
    print("first", res1, keys1, "second", res2, keys2)
    assert {"m2", "m3"} <= keys2, (res1, keys1, res2, keys2)

