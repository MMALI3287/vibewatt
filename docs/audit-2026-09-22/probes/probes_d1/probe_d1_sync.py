from __future__ import annotations

import json
import os
import sys
import time
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from d1util import JST, projects_root, rec, write_jsonl  # noqa: E402

from vibewatt import ingest, store  # noqa: E402


def _cost(_t):
    return 1.0


def _rows(conn):
    return conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]


def _sum_input(conn):
    return conn.execute("SELECT COALESCE(SUM(input),0) FROM turns").fetchone()[0]


def test_partial_trailing_line_then_completed(tmp_path):
    root = projects_root(tmp_path)
    full = json.dumps(rec("m2", "r2", inp=7)) + "\n"
    half = full[: len(full) // 2]
    path = write_jsonl(root / "p" / "a.jsonl", [rec(inp=3)], trailing=half)
    with store.connect(tmp_path / "db.sqlite") as conn:
        first = store.sync_files(conn, ingest.discover(), JST, _cost)
        assert (_rows(conn), _sum_input(conn)) == (1, 3)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(full[len(full) // 2:])
        second = store.sync_files(conn, ingest.discover(), JST, _cost)
        assert (_rows(conn), _sum_input(conn)) == (2, 10)
        third = store.sync_files(conn, ingest.discover(), JST, _cost)
        assert (_rows(conn), _sum_input(conn)) == (2, 10)
    assert (first.parsed, second.parsed, third.parsed) == (1, 1, 0)


def test_appended_file_no_double_count(tmp_path):
    root = projects_root(tmp_path)
    path = write_jsonl(root / "p" / "a.jsonl", [rec(inp=3), rec(inp=3)])
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec(inp=3)) + "\n")  # another content block of m1
            fh.write(json.dumps(rec("m2", "r2", inp=4)) + "\n")
        store.sync_files(conn, ingest.discover(), JST, _cost)
        assert (_rows(conn), _sum_input(conn)) == (2, 7)


def test_truncated_file_keeps_rows(tmp_path):
    root = projects_root(tmp_path)
    path = write_jsonl(root / "p" / "a.jsonl", [rec(inp=3), rec("m2", "r2", inp=4)])
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        write_jsonl(path, [rec(inp=3)])
        res = store.sync_files(conn, ingest.discover(), JST, _cost)
        assert res.parsed == 1
        assert (_rows(conn), _sum_input(conn)) == (2, 7)


def test_rewrite_same_size_same_mtime_is_skipped(tmp_path):
    root = projects_root(tmp_path)
    path = write_jsonl(root / "p" / "a.jsonl", [rec(inp=3)])
    st = path.stat()
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        write_jsonl(path, [rec(inp=4)])  # same byte length
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert path.stat().st_size == st.st_size
        res = store.sync_files(conn, ingest.discover(), JST, _cost)
        print("same size+mtime rewrite parsed", res.parsed, "input", _sum_input(conn))


def test_deleted_file_rows_survive(tmp_path):
    root = projects_root(tmp_path)
    path = write_jsonl(root / "p" / "a.jsonl", [rec(inp=3)])
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        os.remove(path)
        store.sync_files(conn, ingest.discover(), JST, _cost)
        assert _rows(conn) == 1


def test_tz_change_rebuckets_pruned_rows(tmp_path):
    root = projects_root(tmp_path)
    path = write_jsonl(root / "p" / "a.jsonl", [rec(ts="2026-09-15T20:00:00Z")])
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), timezone.utc, _cost)
        os.remove(path)
        store.sync_files(conn, ingest.discover(), JST, _cost)
        assert conn.execute("SELECT day FROM turns").fetchone()[0] == "2026-09-16"


def test_dst_zone_day_bucketing(tmp_path):
    from zoneinfo import ZoneInfo

    try:
        ny = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        import pytest

        pytest.skip("no tzdata")
    root = projects_root(tmp_path)
    # 03:30Z on 2026-01-15 is 22:30 EST on the 14th; 03:30Z on 2026-07-15 is 23:30 EDT on the 14th.
    write_jsonl(root / "p" / "a.jsonl", [rec("w", "w", ts="2026-01-15T03:30:00Z"),
                                           rec("s", "s", ts="2026-07-15T03:30:00Z")])
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), ny, _cost)
        days = dict(conn.execute("SELECT msg_id, day FROM turns"))
    assert days == {"w": "2026-01-14", "s": "2026-07-14"}


def _synthetic_tree(root: Path, files=200, lines=250):
    root.mkdir(parents=True, exist_ok=True)
    for f in range(files):
        recs = []
        for i in range(lines):
            # ~2.5 content-block lines per response, like real logs
            n = i // 2
            recs.append(rec(f"m{f}_{n}", f"r{f}_{n}", session=f"s{f}",
                            ts=f"2026-09-{1 + (i % 28):02d}T10:00:00Z", inp=10, out=5))
        write_jsonl(root / f"proj{f % 10}" / f"sess{f}.jsonl", recs)


def test_gate_unchanged_resync_under_one_second(tmp_path):
    root = projects_root(tmp_path)
    _synthetic_tree(root)
    files = ingest.discover()
    assert len(files) == 200
    db = tmp_path / "db.sqlite"
    t0 = time.perf_counter()
    with store.connect(db) as conn:
        first = store.sync_files(conn, files, JST, _cost)
    t1 = time.perf_counter()
    with store.connect(db) as conn:
        counts_before = dict(conn.execute("SELECT path, turn_count FROM files"))
    t2 = time.perf_counter()
    with store.connect(db) as conn:
        again = store.sync_files(conn, ingest.discover(), JST, _cost)
        counts_after = dict(conn.execute("SELECT path, turn_count FROM files"))
        n = _rows(conn)
    t3 = time.perf_counter()
    print(f"first sync {t1 - t0:.2f}s parsed={first.parsed} turns={first.turns} "
          f"dups={first.duplicates}; unchanged resync {t3 - t2:.3f}s skipped={again.skipped} rows={n}")
    assert again.parsed == 0 and again.skipped == 200
    assert counts_after == counts_before
    assert t3 - t2 < 1.0
