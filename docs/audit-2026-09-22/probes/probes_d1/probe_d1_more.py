from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from d1util import JST, projects_root, rec, write_jsonl  # noqa: E402

from vibewatt import ingest, quota, store  # noqa: E402
from vibewatt.sources import load  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _cost(_t):
    return 1.0


# --- subagent transcripts ---------------------------------------------------

def test_nested_subagent_files_discovered_and_attributed(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "C--work-demo" / "sess1.jsonl", [rec("m1", "r1", session="sess1")])
    write_jsonl(root / "C--work-demo" / "sess1" / "subagents" / "agent-a1.jsonl",
                [rec("m2", "r2", session="sess1", isSidechain=True, agentId="a1")])
    files = ingest.discover()
    assert len(files) == 2
    turns, _ = load(files)
    sub = [t for t in turns if t.sidechain]
    assert len(sub) == 1 and sub[0].session == "sess1" and sub[0].project == "demo"


def test_nested_subagent_without_cwd_project_name(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "C--work-demo" / "sess1.jsonl", [rec("m1", "r1", session="sess1", cwd=None)])
    write_jsonl(root / "C--work-demo" / "sess1" / "subagents" / "agent-a1.jsonl",
                [rec("m2", "r2", session="sess1", cwd=None, isSidechain=True)])
    turns, _ = load(ingest.discover())
    print("projects without cwd:", sorted((t.project, t.sidechain) for t in turns))


# --- cowork envelope ---------------------------------------------------------

def _cowork(tmp_path, name="audit.jsonl"):
    return (tmp_path / "appdata" / "Claude" / "local-agent-mode-sessions" / "acct" / "space"
            / "local1" / name)


def test_cowork_envelope_dedup_and_attribution(tmp_path):
    base = {"type": "assistant", "requestId": "r9", "_audit_timestamp": "2026-09-15T02:00:00Z",
            "session_id": "c1", "_audit_hmac": "x", "client_platform": "desktop_app",
            "message": {"id": "m9", "model": "claude-opus-5",
                        "usage": {"input_tokens": 30, "output_tokens": 5}}}
    write_jsonl(_cowork(tmp_path), [base, base, dict(base, requestId="r10",
                                                     message=dict(base["message"], id="m10"))])
    turns, dups = load(ingest.discover())
    assert (len(turns), dups) == (2, 1)
    t = turns[0]
    assert (t.source, t.session, t.project) == ("cowork", "c1", "space")
    assert t.ts == datetime(2026, 9, 15, 2, tzinfo=timezone.utc)


# --- missing message.id with requestId, across sync runs ---------------------

def test_missing_msg_id_with_request_id_across_runs(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "a.jsonl", [rec(msg_id=None, req="rX", session="s1")])
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        write_jsonl(root / "p" / "b.jsonl", [rec(msg_id=None, req="rX", session="s2")])
        store.sync_files(conn, ingest.discover(), JST, _cost)
        stored = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    live, _ = load(ingest.discover())
    print("live", len(live), "store", stored)
    assert stored == len(live)


# --- migrations built with the real older code -------------------------------

def _old_store(tmp_path, commit):
    src = subprocess.run(["git", "show", f"{commit}:vibewatt/store.py"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    path = tmp_path / f"old_store_{commit}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"vibewatt._old_store_{commit}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _W:
    def __init__(self, label, util):
        self.label, self.utilization, self.resets_at = label, util, None


class _Q:
    def __init__(self, ts):
        self.fetched_at = ts
        self.windows = [_W("5-hour", 10.0), _W("7-day", 20.0)]


@pytest.mark.parametrize("commit,expected_old", [("971211e", 1), ("b80b95e", 2), ("620f5cc", 3),
                                                   ("a15d8a1", 3)])
def test_forward_migration_from_real_old_store(tmp_path, commit, expected_old):
    old = _old_store(tmp_path, commit)
    db = tmp_path / "db.sqlite"
    with old.connect(db) as conn:
        conn.execute("INSERT INTO turns (msg_id, request_id, ts, day, source, project, session,"
                     " model, cost) VALUES ('m','r','2026-09-15T00:00:00+00:00','2026-09-15',"
                     "'claude-code','p','s','claude-opus-5',1.5)")
        conn.execute("INSERT INTO sessions (id, harvested, cost) VALUES ('cloud1', 1, 2.0)")
        conn.execute("INSERT INTO prompts VALUES ('s', NULL, 'title')")
        conn.execute("CREATE TABLE user_notes (x TEXT)")
        conn.execute("INSERT INTO user_notes VALUES ('mine')")
        if hasattr(old, "upsert_quota_samples"):
            q = _Q(datetime(2026, 9, 15, tzinfo=timezone.utc))
            old.upsert_quota_samples(conn, q)
            old.upsert_quota_samples(conn, q)
        ver = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    assert int(ver[0]) == expected_old
    with store.connect(db) as conn:
        assert store.schema_version(conn) == store.SCHEMA_VERSION
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("turns", "sessions", "prompts", "user_notes", "quota_samples",
                            "files", "findings", "tool_reads", "tool_read_files")}
    print(commit, counts)
    assert counts["turns"] == counts["sessions"] == counts["prompts"] == counts["user_notes"] == 1
    if hasattr(old, "upsert_quota_samples"):
        assert counts["quota_samples"] == 2


def test_migration_step_failure_leaves_version_and_retries(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    raw = sqlite3.connect(db)
    raw.executescript(store.SCHEMA)
    raw.execute("INSERT INTO meta VALUES ('schema', '3')")
    store._v2_files_and_quota_samples(raw)
    raw.commit()
    raw.close()

    real_v4 = store._v4_analysis

    def boom(conn):
        real_v4(conn)  # every DDL ran, then the step fails before meta is stamped
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setitem(store.MIGRATIONS, 4, boom)
    with pytest.raises(sqlite3.OperationalError):
        with store.connect(db):
            pass
    raw = sqlite3.connect(db)
    ver = raw.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]
    cols = [r[1] for r in raw.execute("PRAGMA table_info(findings)")]
    raw.close()
    monkeypatch.undo()
    print("after failed v4: schema", ver, "partial findings cols", cols)
    with store.connect(db) as conn:
        cols2 = [r[1] for r in conn.execute("PRAGMA table_info(findings)")]
        ver2 = store.schema_version(conn)
    print("after retry: schema", ver2, "findings cols", cols2)
    assert ver == "3"
    assert "scope" in cols2, "retry kept the half-created findings table without its columns"


# --- quota samples ------------------------------------------------------------

def test_stale_statusline_records_zero_at_capture_time(tmp_path):
    captured = datetime.now(timezone.utc) - timedelta(hours=6)
    dump = tmp_path / "statusline.json"
    dump.write_text(json.dumps({
        "captured_at": captured.isoformat(),
        "rate_limits": {"five_hour": {"utilization": 80.0,
                                      "resets_at": (captured + timedelta(hours=1)).isoformat()}},
    }), encoding="utf-8")
    q, _ = quota.read({"quota": True, "statusline_cache_path": str(dump)})
    with store.connect() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT ts, label, utilization FROM quota_samples")]
    print("stored:", rows, "reading ts", captured.isoformat())
    assert all(r[2] == 80.0 for r in rows), "sample at capture time must hold the captured value"


def test_quota_endpoint_path_samples_with_http_mocked(tmp_path, monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"five_hour": {"utilization": 42.0, "resets_at": None},
                               "seven_day": {"utilization": 7.0, "resets_at": None}}).encode()

    calls = []
    monkeypatch.setattr(quota, "read_token", lambda: "tok")
    monkeypatch.setattr(quota.urllib.request, "urlopen", lambda req, timeout=0: calls.append(req) or Resp())
    q1, _ = quota.read({"quota": True})
    q2, _ = quota.read({"quota": True})
    with store.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0]
    print("windows", [(w.label, w.utilization) for w in q1.windows], "samples", n, "calls", len(calls))
    assert len(calls) == 2 and n >= 2


# --- history rollup: realistic prune of a day still touched by a surviving file ---

def test_history_prune_with_session_spanning_midnight(tmp_path):
    import os

    from vibewatt.cli import build_report

    root = projects_root(tmp_path)
    # s2 worked on Aug 1 (JST) only; s1 crossed midnight Aug 1 -> Aug 2, so its file is newer.
    b = write_jsonl(root / "p" / "s2.jsonl", [rec("b1", "rb1", ts="2026-08-01T01:00:00Z",
                                                   session="s2", inp=5000, out=0)])
    write_jsonl(root / "p" / "s1.jsonl", [
        rec("a1", "ra1", ts="2026-08-01T14:30:00Z", session="s1", inp=100, out=0),   # 23:30 JST Aug 1
        rec("a2", "ra2", ts="2026-08-01T15:30:00Z", session="s1", inp=100, out=0),   # 00:30 JST Aug 2
    ])
    cfg = {"offline": True, "quota": False, "history": True}
    before, *_ = build_report(cfg, JST)
    os.remove(b)  # cleanupPeriodDays prunes by mtime: s2 is older than s1
    after, *_ = build_report(cfg, JST)
    from datetime import date
    d = date(2026, 8, 1)
    print("Aug 1 input before", before.by_day[d].input, "after prune", after.by_day[d].input,
          "total before", before.total.input, "after", after.total.input)
    assert after.total.input == before.total.input


def test_history_restore_turns_unpriced_into_zero_cost(tmp_path):
    import os

    from vibewatt import pricing
    from vibewatt.cli import build_report, serialize

    pricing._remote = None
    root = projects_root(tmp_path)
    f = write_jsonl(root / "p" / "a.jsonl", [rec("u1", "ru1", ts="2026-08-01T03:00:00Z",
                                                  model="claude-haiku-9", inp=1_000_000, out=0)])
    cfg = {"offline": True, "quota": False, "history": True}
    live, *_ = build_report(cfg, JST)
    os.remove(f)
    restored, *_ = build_report(cfg, JST)
    print("live unpriced", live.total.unpriced, "cost", live.total.cost, "unknown", live.unknown_models,
          "| restored unpriced", restored.total.unpriced, "cost", restored.total.cost,
          "unknown", restored.unknown_models, "restored_days", restored.restored_days)
    assert restored.total.unpriced == live.total.unpriced == 1
