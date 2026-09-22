from __future__ import annotations

import os
import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from d1util import JST, projects_root, rec, write_jsonl  # noqa: E402

from vibewatt import ingest, store  # noqa: E402
from vibewatt.cli import build_report  # noqa: E402
from vibewatt.sources import load  # noqa: E402

OFFLINE = {"offline": True, "quota": False, "history": False}


def _cost(_t):
    return 1.0


def _rows(conn):
    return conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]


def test_within_file_dedup_live_and_store(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "a.jsonl", [rec(), rec(), rec(), rec("m2", "r2")])
    turns, dups = load(ingest.discover())
    assert (len(turns), dups) == (2, 2)
    with store.connect(tmp_path / "db.sqlite") as conn:
        res = store.sync_files(conn, ingest.discover(), JST, _cost)
        assert _rows(conn) == 2
    assert res.duplicates == 2


def test_across_files_resume_copy(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "orig.jsonl", [rec(), rec("m2", "r2")])
    # --resume/--continue replays prior history into a new file.
    write_jsonl(root / "p" / "resumed.jsonl",
                [rec(session="s2"), rec("m2", "r2", session="s2"), rec("m3", "r3", session="s2")])
    turns, dups = load(ingest.discover())
    assert (len(turns), dups) == (3, 2)


def test_across_sync_runs_new_file_with_replayed_history(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "orig.jsonl", [rec(), rec("m2", "r2")])
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        write_jsonl(root / "p" / "resumed.jsonl",
                    [rec(session="s2"), rec("m2", "r2", session="s2"),
                     rec("m3", "r3", session="s2")])
        second = store.sync_files(conn, ingest.discover(), JST, _cost)
        assert _rows(conn) == 3
        owner = conn.execute("SELECT session FROM turns WHERE msg_id='m1'").fetchone()[0]
    # Cross-run repeats are collapsed by the primary key, not counted.
    print("second.duplicates", second.duplicates, "m1 owner", owner)


def test_missing_ids_live_vs_store(tmp_path):
    """Content-block repeats of a response that lacks message.id and requestId."""
    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "a.jsonl",
                [rec(msg_id=None, req=None), rec(msg_id=None, req=None),
                 rec(msg_id=None, req=None)])
    turns, dups = load(ingest.discover())
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        stored = _rows(conn)
    report, *_ = build_report(OFFLINE, JST)
    print("live turns", len(turns), "dups", dups, "store rows", stored,
          "report responses", report.total.turns)
    # The two paths the dashboard reads must agree on a response count.
    assert len(turns) == stored


def test_missing_request_id_only_dedups(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "a.jsonl", [rec(req=None), rec(req=None)])
    turns, dups = load(ingest.discover())
    assert (len(turns), dups) == (1, 1)


def test_history_rollup_double_counts_after_tz_change(tmp_path):
    """history.json keys days in the report tz; a tz change re-adds shifted days."""
    root = projects_root(tmp_path)
    # 20:00Z on the 15th is the 16th in JST.
    write_jsonl(root / "p" / "a.jsonl", [rec(inp=1000, out=0)])
    cfg = {"offline": True, "quota": False, "history": True}
    utc_report, *_ = build_report(cfg, timezone.utc)
    jst_report, *_ = build_report(cfg, JST)
    print("utc input", utc_report.total.input, "jst input", jst_report.total.input,
          "restored", sorted(map(str, jst_report.restored_days)))
    assert jst_report.total.input == utc_report.total.input == 1000


def test_history_rollup_partial_prune_loses_usage(tmp_path):
    """Day X is covered by two files; one is pruned. The rollup must keep both."""
    root = projects_root(tmp_path)
    a = write_jsonl(root / "p" / "a.jsonl",
                    [rec("m1", "r1", ts="2026-08-01T03:00:00Z", model="claude-opus-5", inp=100, out=0),
                     rec("m3", "r3", ts="2026-08-05T03:00:00Z", model="claude-opus-5", inp=1, out=0)])
    b = write_jsonl(root / "p" / "b.jsonl",
                    [rec("m2", "r2", ts="2026-08-01T04:00:00Z", model="claude-sonnet-4-6",
                         inp=500, out=0, session="s2")])
    cfg = {"offline": True, "quota": False, "history": True}
    full, *_ = build_report(cfg, JST)
    assert full.total.input == 601
    os.remove(b)  # Claude Code prunes b by mtime; a survives (touched later)
    after, *_ = build_report(cfg, JST)
    print("before", full.total.input, "after prune", after.total.input)
    assert a.exists()
    assert after.total.input == 601


def test_history_rollup_partial_prune_same_model_overwrites_blob(tmp_path):
    from vibewatt import history

    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "a.jsonl",
                [rec("m1", "r1", ts="2026-08-01T03:00:00Z", inp=100, out=0),
                 rec("m3", "r3", ts="2026-08-05T03:00:00Z", inp=1, out=0)])
    b = write_jsonl(root / "p" / "b.jsonl",
                    [rec("m2", "r2", ts="2026-08-01T04:00:00Z", inp=500, out=0, session="s2")])
    cfg = {"offline": True, "quota": False, "history": True}
    build_report(cfg, JST)
    stored_before = history.load()["days"]["2026-08-01|claude-opus-5"]["input"]
    os.remove(b)
    build_report(cfg, JST)
    stored_after = history.load()["days"]["2026-08-01|claude-opus-5"]["input"]
    print("blob before", stored_before, "blob after", stored_after)
    assert stored_after == stored_before == 600


def test_session_attribution_depends_on_sync_order(tmp_path):
    root = projects_root(tmp_path)
    write_jsonl(root / "p" / "orig.jsonl", [rec()])
    write_jsonl(root / "p" / "resumed.jsonl", [rec(session="s2"), rec("m3", "r3", session="s2")])
    with store.connect(tmp_path / "fresh.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        fresh = conn.execute("SELECT session FROM turns WHERE msg_id='m1'").fetchone()[0]
    os.remove(root / "p" / "resumed.jsonl")
    with store.connect(tmp_path / "incr.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        write_jsonl(root / "p" / "resumed.jsonl", [rec(session="s2"), rec("m3", "r3", session="s2")])
        store.sync_files(conn, ingest.discover(), JST, _cost)
        incr = conn.execute("SELECT session FROM turns WHERE msg_id='m1'").fetchone()[0]
    print("fresh owner", fresh, "incremental owner", incr)
    assert fresh == incr
