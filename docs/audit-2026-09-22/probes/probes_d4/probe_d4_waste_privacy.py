"""d4 probes: 7.9 waste rules, cross-OS path normalization, privacy of tool_reads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from vibewatt import store
from vibewatt.aggregate import cost_of
from vibewatt.analysis import analyze, waste
from vibewatt.ingest.tool_reads import read_tools

UTC = timezone.utc
NOW = datetime(2026, 9, 19, tzinfo=UTC)


def rec(i, session, cwd, file_path, tool="Read", ts_hour=1, tool_id=None, model="claude-opus-5"):
    return {
        "type": "assistant",
        "timestamp": f"2026-09-01T{ts_hour:02d}:00:{i:02d}Z",
        "sessionId": session,
        "cwd": cwd,
        "requestId": f"req{session}{i}",
        "message": {
            "id": f"msg{session}{i}",
            "model": model,
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "content": [
                {"type": "tool_use", "id": tool_id or f"t{session}{i}", "name": tool,
                 "input": {"file_path": file_path}},
            ],
        },
    }


def write(path: Path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def hashes(path: Path):
    return [r["path_hash"] for r in read_tools("claude-code", path)]


def test_windows_forward_slash_mixed_case_relative_and_dotdot_collapse(tmp_path):
    p = tmp_path / "w.jsonl"
    cwd = "C:\\Users\\Dev\\Proj"
    write(p, [
        rec(0, "w", cwd, "C:\\Users\\Dev\\Proj\\src\\Main.py"),
        rec(1, "w", cwd, "c:/users/dev/proj/src/main.py"),
        rec(2, "w", cwd, "src\\Main.py"),
        rec(3, "w", cwd, "src/sub/../Main.py"),
        rec(4, "w", cwd, "C:/Users/Dev/Proj/./src/MAIN.PY"),
    ])
    hs = hashes(p)
    print("windows variants distinct hashes:", len(set(hs)))
    assert len(set(hs)) == 1


def test_posix_is_case_sensitive_and_relative_resolves(tmp_path):
    p = tmp_path / "p.jsonl"
    write(p, [
        rec(0, "p", "/home/dev/proj", "/home/dev/proj/src/a.py"),
        rec(1, "p", "/home/dev/proj", "src/a.py"),
        rec(2, "p", "/home/dev/proj", "./src/../src/a.py"),
        rec(3, "p", "/home/dev/proj", "/home/dev/proj/src/A.py"),
    ])
    hs = hashes(p)
    print("posix hashes:", [h[:8] for h in hs])
    assert hs[0] == hs[1] == hs[2] and hs[3] != hs[0]


def test_same_path_in_two_sessions_is_not_repeated(tmp_path):
    p = tmp_path / "two.jsonl"
    write(p, [
        rec(0, "a", "/x", "/x/f.py"), rec(1, "a", "/x", "/x/f.py"),
        rec(2, "b", "/x", "/x/f.py"),
    ])
    reads = list(read_tools("claude-code", p))
    by = {}
    for r in reads:
        by.setdefault(r["session"], []).append(dict(r, day="2026-09-01"))
    assert not waste.detect("a", [], by["a"]) and not waste.detect("b", [], by["b"])
    assert reads[0]["path_hash"] != reads[2]["path_hash"]


def test_non_read_tools_are_ignored(tmp_path):
    p = tmp_path / "e.jsonl"
    write(p, [rec(i, "e", "/x", "/x/f.py", tool=t) for i, t in enumerate(["Edit", "Write", "Grep", "Glob"])])
    assert list(read_tools("claude-code", p)) == []


def test_privacy_after_sync_db_has_only_hashes(tmp_path):
    secret_dir = "C:\\Users\\SecretUser\\TopSecretProject"
    p = tmp_path / "projects" / "demo" / "priv.jsonl"
    p.parent.mkdir(parents=True)
    records = [rec(i, "priv", secret_dir, f"{secret_dir}\\src\\credentials_{i % 2}.env") for i in range(6)]
    records.append(rec(9, "priv", "/srv/hidden-posix", "/srv/hidden-posix/keys/id_rsa"))
    write(p, records)
    db = tmp_path / "data.db"
    with store.connect(db) as conn:
        store.sync_files(conn, [("claude-code", p)], UTC, cost_of)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tool_reads)")]
        rows = [tuple(r) for r in conn.execute("SELECT * FROM tool_reads")]
        result = analyze(conn, UTC, now=NOW)
        findings_text = json.dumps(result)
        findings_db = " ".join(r[0] for r in conn.execute("SELECT detail_json FROM findings"))
    print("tool_reads columns:", cols, "rows:", len(rows))
    print("sample row:", rows[0])
    blob = json.dumps(rows)
    for literal in ("credentials", ".env", "SecretUser", "src", "id_rsa", "keys"):
        assert literal not in blob, literal
        assert literal not in findings_text and literal not in findings_db, literal
    # project name column holds cwd basename, same as turns.project
    print("project values:", sorted({r[4] for r in rows}))
    # raw file scan of DB (including WAL) for literal target paths
    raw = db.read_bytes() + (db.with_name("data.db-wal").read_bytes() if db.with_name("data.db-wal").exists() else b"")
    assert b"credentials_" not in raw and b"id_rsa" not in raw
    rr = [f for f in result["findings"] if f["rule"] == "repeated_reads"]
    print("repeated reads:", [f["metrics"] for f in rr])
    assert rr and rr[0]["metrics"]["files"] == 2 and rr[0]["metrics"]["max_reads"] == 3


def test_waste_rules_one_fixture_each_via_analyze(tmp_path):
    db = tmp_path / "w.db"
    cols = ["msg_id", "request_id", "ts", "day", "source", "project", "session", "model",
            "input", "cache_5m", "cache_1h", "cache_read", "output", "thinking", "web_search",
            "sidechain", "fast", "geo", "cost"]

    def t(session, i, **kw):
        base = dict(msg_id=f"{session}{i}", request_id="r", ts=f"2026-09-01T0{i}:00:00+00:00",
                    day="2026-09-01", source="claude-code", project="demo", session=session,
                    model="claude-sonnet-4-5", input=100, cache_5m=0, cache_1h=0, cache_read=0,
                    output=100, thinking=0, web_search=0, sidechain=0, fast=0, geo=None, cost=0.01)
        base.update(kw)
        return [base[c] for c in cols]

    rows = []
    rows += [t("ratio", i, cache_read=50_000, output=10) for i in range(5)]  # 250k cache, 50 output
    rows += [t("sub", i, sidechain=1 if i < 3 else 0) for i in range(5)]
    rows += [t("long", i, output=150, ts=f"2026-09-01T0{i * 2}:00:00+00:00") for i in range(5)]
    rows += [t("ok", i, output=5000, cache_read=1000) for i in range(5)]
    with store.connect(db) as conn:
        conn.executemany(f"INSERT INTO turns ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
        res = analyze(conn, UTC, now=NOW)
    got = sorted((f["subject"], f["rule"]) for f in res["findings"] if f["kind"] == "waste")
    print("waste findings:", got)
    assert ("ratio", "cache_to_output") in got
    assert ("sub", "subagent_heavy") in got
    assert ("long", "long_low_output") in got
    assert not [g for g in got if g[0] == "ok"]
