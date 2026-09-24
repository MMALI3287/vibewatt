"""Phase 6.5b gate: the dedup rule and the store as the only source of numbers."""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import JST

from vibewatt import config, store
from vibewatt.aggregate import build_blocks, cost_of, from_store
from vibewatt.sources import CLAUDE_CODE, COWORK, load


def line(
    msg="m1",
    req="r1",
    ts="2026-09-15T01:00:00Z",
    session="s1",
    output=5,
    sidechain=False,
    model="claude-opus-5",
    cwd="/work/demo",
    request_key="requestId",
    input_tokens=10,
    **extra,
):
    rec = {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": session,
        "cwd": cwd,
        "isSidechain": sidechain,
        "message": {
            "model": model,
            "usage": {"input_tokens": input_tokens, "output_tokens": output},
        },
    }
    if msg:
        rec["message"]["id"] = msg
    if req:
        rec[request_key] = req
    rec.update(extra)
    return json.dumps(rec)


def write(path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def sync(conn, files, tz=UTC):
    return store.sync_files(conn, files, tz, cost_of)


def outputs(conn):
    return conn.execute(
        "SELECT COALESCE(SUM(output), 0), COUNT(*) FROM turns"
    ).fetchone()


def report(conn, **kw):
    return from_store(conn, UTC, **kw)


# --- the dedup rule -----------------------------------------------------------


@pytest.mark.parametrize("order", [(3, 956), (956, 3)])
def test_placeholder_then_final_keeps_the_final_counts(tmp_path, order):
    f = write(tmp_path / "a.jsonl", *(line(output=o) for o in order))
    with store.connect(tmp_path / "db") as conn:
        sync(conn, [(CLAUDE_CODE, f)])
        assert tuple(outputs(conn)) == (956, 1)
        assert report(conn).total.output == 956
    turns, dropped = load([(CLAUDE_CODE, f)])
    assert [t.output for t in turns] == [956] and dropped == 1


def test_placeholder_and_final_in_separate_syncs(tmp_path):
    f = write(tmp_path / "a.jsonl", line(output=3))
    with store.connect(tmp_path / "db") as conn:
        sync(conn, [(CLAUDE_CODE, f)])
        write(f, line(output=3), line(output=956))
        sync(conn, [(CLAUDE_CODE, f)])
        assert tuple(outputs(conn)) == (956, 1)


@pytest.mark.parametrize("first", ["main", "aside"])
def test_aside_replay_of_a_parent_message_counts_once(tmp_path, first):
    main = write(tmp_path / "main.jsonl", line(msg="m1", req="r1", output=40))
    aside = write(
        tmp_path / "aside.jsonl",
        line(msg="m1", req="r-aside", output=40, sidechain=True),
        line(msg="m2", req="r2", output=7, sidechain=True),
    )
    files = {"main": (CLAUDE_CODE, main), "aside": (CLAUDE_CODE, aside)}
    order = [files[first], files["aside" if first == "main" else "main"]]
    with store.connect(tmp_path / "db") as conn:
        for f in order:  # separate syncs, so the store has to resolve it
            sync(conn, [f])
        assert tuple(outputs(conn)) == (47, 2)
        rows = dict(conn.execute("SELECT msg_id, sidechain FROM turns").fetchall())
        assert rows == {"m1": 0, "m2": 1}


def test_gateway_without_request_id_keys_on_session_and_time(tmp_path):
    f = write(
        tmp_path / "gw.jsonl",
        line(msg="msg_gw", req=None, session="a", ts="2026-09-15T01:00:00Z"),
        line(msg="msg_gw", req=None, session="a", ts="2026-09-15T01:00:00Z"),
        line(msg="msg_gw", req=None, session="b", ts="2026-09-15T01:00:00Z"),
        line(msg="msg_gw", req=None, session="a", ts="2026-09-15T02:00:00Z"),
    )
    with store.connect(tmp_path / "db") as conn:
        sync(conn, [(CLAUDE_CODE, f)])
        assert outputs(conn)[1] == 3


def test_file_order_does_not_change_attribution(tmp_path):
    original = line(
        msg="m1", req="r1", session="orig", ts="2026-09-15T01:00:00Z", cwd="/work/alpha"
    )
    replay = line(
        msg="m1",
        req="r1",
        session="fork",
        ts="2026-09-15T03:00:00Z",
        cwd="/work/beta",
        output=9,
    )
    a = write(tmp_path / "a.jsonl", original)
    b = write(tmp_path / "b.jsonl", replay)
    results = []
    for order in ([a, b], [b, a]):
        with store.connect(tmp_path / f"db-{order[0].stem}") as conn:
            for f in order:
                sync(conn, [(CLAUDE_CODE, f)])
            results.append(
                tuple(
                    conn.execute(
                        "SELECT session, project, output, ts FROM turns"
                    ).fetchone()
                )
            )
    assert results[0] == results[1] == ("orig", "alpha", 9, "2026-09-15T01:00:00+00:00")


def test_cowork_audit_and_transcript_copy_count_once(tmp_path):
    audit = write(
        tmp_path / "audit.jsonl",
        line(msg="m9", req="r9", request_key="request_id", output=30),
    )
    transcript = write(tmp_path / "t.jsonl", line(msg="m9", req="r9", output=30))
    for order in (
        [(COWORK, audit), (CLAUDE_CODE, transcript)],
        [(CLAUDE_CODE, transcript), (COWORK, audit)],
    ):
        with store.connect(tmp_path / f"db-{order[0][0]}") as conn:
            for f in order:
                sync(conn, [f])
            assert tuple(outputs(conn)) == (30, 1)


def test_pre_6_5b_row_without_request_id_is_absorbed(tmp_path):
    f = write(
        tmp_path / "audit.jsonl",
        line(msg="m9", req="r9", request_key="request_id", output=30),
    )
    with store.connect(tmp_path / "db") as conn:
        conn.execute(
            f"INSERT INTO turns ({store.TURN_COLUMNS}) VALUES "
            "('m9','','2026-09-15T01:00:00+00:00','2026-09-15','cowork','demo','s1',"
            "'claude-opus-5',10,0,0,0,3,0,0,0,0,NULL,1.0,NULL,1)"
        )
        sync(conn, [(COWORK, f)])
        rows = [tuple(r) for r in conn.execute("SELECT request_id, output FROM turns")]
        assert rows == [("r9", 30)]


# --- the store is the only source ----------------------------------------------


def _tree(tmp_path):
    days = [date(2026, 9, d) for d in (10, 11, 12)]
    files = []
    for i, day in enumerate(days):
        for j in range(2):
            ts = f"{day}T0{j + 1}:00:00Z"
            files.append(
                (
                    CLAUDE_CODE,
                    write(
                        tmp_path / "logs" / f"s{i}{j}.jsonl",
                        line(
                            msg=f"m{i}{j}",
                            req=f"r{i}{j}",
                            ts=ts,
                            session=f"s{i}{j}",
                            output=3,
                        ),
                        line(
                            msg=f"m{i}{j}",
                            req=f"r{i}{j}",
                            ts=ts,
                            session=f"s{i}{j}",
                            output=100 + i,
                        ),
                    ),
                )
            )
    # A session spanning midnight: its second response is on the next day.
    files.append(
        (
            CLAUDE_CODE,
            write(
                tmp_path / "logs" / "span.jsonl",
                line(
                    msg="x1",
                    req="x1",
                    ts="2026-09-10T23:30:00Z",
                    session="span",
                    output=11,
                ),
                line(
                    msg="x2",
                    req="x2",
                    ts="2026-09-11T00:30:00Z",
                    session="span",
                    output=13,
                ),
            ),
        )
    )
    return files


def test_pruning_any_subset_never_lowers_a_day(tmp_path):
    files = _tree(tmp_path)
    with store.connect(tmp_path / "db") as conn:
        sync(conn, files)
        before = {d: b.output for d, b in report(conn).by_day.items()}
    for k in (1, 3, len(files)):
        for pruned in itertools.islice(itertools.combinations(files, k), 6):
            db = tmp_path / f"db-{k}-{abs(hash(pruned))}"
            with store.connect(db) as conn:
                sync(conn, files)
                for _, path in pruned:
                    moved = path.with_suffix(".gone")
                    path.rename(moved)
                try:
                    sync(conn, [f for f in files if f not in pruned])
                    after = {d: b.output for d, b in report(conn).by_day.items()}
                finally:
                    for _, path in pruned:
                        path.with_suffix(".gone").rename(path)
            assert after == before


def test_history_json_fills_only_what_the_store_lacks(tmp_path):
    files = _tree(tmp_path)
    history = {
        "schema": 1,
        "days": {
            # Pruned before the store existed: all of it is missing.
            "2026-09-01|claude-opus-5": {
                "responses": 4,
                "output": 400,
                "input": 40,
                "cost": 2.5,
            },
            # Partly pruned: the store has 100+100+11, history saw 300.
            "2026-09-10|claude-opus-5": {
                "responses": 5,
                "output": 300,
                "input": 60,
                "cost": 9.0,
            },
            # The store already has more than history: nothing is added.
            "2026-09-12|claude-opus-5": {
                "responses": 1,
                "output": 5,
                "input": 1,
                "cost": 0.1,
            },
            # A model nobody can price must stay unpriced, never $0 (A-002).
            "2026-09-02|mystery-model-9": {"responses": 2, "output": 50, "cost": 0.0},
        },
    }
    (config.data_dir()).mkdir(parents=True, exist_ok=True)
    (config.data_dir() / "history.json").write_text(
        json.dumps(history), encoding="utf-8"
    )
    with store.connect(tmp_path / "db") as conn:
        sync(conn, files)
        live = {d: b.output for d, b in from_store(conn, UTC).by_day.items()}
        assert conn.execute("SELECT COUNT(*) FROM history_days").fetchone()[0] == 4
        r = report(conn)
        assert r.by_day[date(2026, 9, 1)].output == 400
        assert r.by_day[date(2026, 9, 10)].output == 300
        assert r.by_day[date(2026, 9, 12)].output == live[date(2026, 9, 12)]
        assert (
            r.by_day[date(2026, 9, 2)].unpriced and r.by_day[date(2026, 9, 2)].cost == 0
        )
        assert "mystery-model-9" in r.unknown_models
        assert date(2026, 9, 1) in r.restored_days
        # A second sync never imports again, and a filter by project skips history.
        sync(conn, files)
        assert report(conn).total.output == r.total.output
        assert date(2026, 9, 1) not in report(conn, project="demo").by_day


def test_timezone_change_does_not_double_count(tmp_path):
    files = _tree(tmp_path)
    with store.connect(tmp_path / "db") as conn:
        sync(conn, files, UTC)
        utc_total = report(conn).total.output
        sync(conn, files, JST)
        jst = from_store(conn, JST)
        assert jst.total.output == utc_total
        assert date(2026, 9, 10) in jst.by_day  # 23:30Z is 08:30 JST the next day
        assert jst.by_hour[8].output


def test_blocks_from_store_match_blocks_from_turns(tmp_path):
    base = datetime(2026, 9, 15, 0, 7, tzinfo=UTC)
    minutes = [0, 20, 130, 290, 301, 305, 700, 1010, 1011, 1400]
    f = write(
        tmp_path / "b.jsonl",
        *(
            line(
                msg=f"m{i}",
                req=f"r{i}",
                ts=(base + timedelta(minutes=m)).isoformat(),
                output=i + 1,
            )
            for i, m in enumerate(minutes)
        ),
    )
    turns, _ = load([(CLAUDE_CODE, f)])
    expected = [
        (b.start, b.end, b.bucket.output, b.last_activity) for b in build_blocks(turns)
    ]
    with store.connect(tmp_path / "db") as conn:
        sync(conn, [(CLAUDE_CODE, f)])
        got = [
            (b.start, b.end, b.bucket.output, b.last_activity)
            for b in report(conn).blocks
        ]
    assert got == expected and len(expected) > 2


def test_changed_size_with_same_mtime_is_reparsed(tmp_path):  # A-018
    f = write(tmp_path / "a.jsonl", line(output=5))
    with store.connect(tmp_path / "db") as conn:
        sync(conn, [(CLAUDE_CODE, f)])
        stamp = f.stat().st_mtime
        write(f, line(output=5), line(msg="m2", req="r2", output=6))
        os.utime(f, (stamp, stamp))
        result = sync(conn, [(CLAUDE_CODE, f)])
        assert result.parsed == 1 and outputs(conn)[1] == 2


def test_include_sidechains_false_drops_subagent_turns(tmp_path):  # A-021
    f = write(
        tmp_path / "a.jsonl",
        line(output=5),
        line(msg="sub", req="rs", output=50, sidechain=True),
    )
    with store.connect(tmp_path / "db") as conn:
        sync(conn, [(CLAUDE_CODE, f)])
        assert report(conn).total.output == 55
        without = report(conn, include_sidechains=False)
        assert without.total.output == 5
        assert without.subagent.output == 50


def test_session_total_equals_sum_of_its_turns(tmp_path, logs):  # A-039
    from vibewatt import ingest

    with store.connect(tmp_path / "db") as conn:
        sync(conn, ingest.discover(), JST)
        for row in store.sessions(conn, limit=100):
            detail = store.session_detail(conn, row["id"])
            assert detail["cost"] == pytest.approx(
                sum(t["cost"] or 0 for t in detail["turns"])
            )
            assert row["cost"] == pytest.approx(detail["cost"])
            assert row["tokens"] == detail["tokens"]


def test_migration_keeps_user_tables_and_turns(tmp_path):  # A-044
    path = tmp_path / "v4.db"
    conn = sqlite3.connect(path)
    conn.executescript(store.SCHEMA)
    store._v2_files_and_quota_samples(conn)
    store._v4_analysis(conn)
    conn.execute("INSERT INTO meta VALUES ('schema', '4')")
    conn.execute("CREATE TABLE my_notes (note TEXT)")
    conn.execute("INSERT INTO my_notes VALUES ('keep me')")
    conn.execute(
        "INSERT INTO turns (msg_id, request_id, ts, day, source, project, session, model,"
        " input, output, cost) VALUES ('m','r','2026-09-15T01:00:00+00:00','2026-09-15',"
        "'claude-code','demo','s','claude-opus-5',1,2,0.5)"
    )
    conn.commit()
    conn.close()
    with store.connect(path) as conn:
        assert store.schema_version(conn) == store.SCHEMA_VERSION
        assert conn.execute("SELECT note FROM my_notes").fetchone()[0] == "keep me"
        assert conn.execute("SELECT output FROM turns").fetchone()[0] == 2
        sync(conn, [])  # the forced rebucket fills hour and builds the rollup
        assert conn.execute("SELECT hour FROM turns").fetchone()[0] == 1
        assert report(conn).total.output == 2


def test_report_endpoints_do_not_read_logs(tmp_path, logs, monkeypatch):
    from fastapi.testclient import TestClient

    from vibewatt import sources
    from vibewatt.api import create_app

    client = TestClient(create_app({"offline": True, "quota": False}))
    first = client.get("/api/summary").json()  # the one-time startup sync
    monkeypatch.setattr(
        sources, "read_file", lambda *a: pytest.fail("a report request read a log file")
    )
    for url in (
        "/api/summary",
        "/api/daily",
        "/api/hourly",
        "/api/blocks",
        "/api/breakdown/model",
        "/api/export?format=csv",
    ):
        assert client.get(url).status_code == 200
    assert client.get("/api/summary").json()["total"] == first["total"]
