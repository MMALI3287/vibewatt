"""Phase 10: Gemini usage from Google Antigravity's conversation databases."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import JST

from vibewatt import ingest, pricing, store
from vibewatt.aggregate import cost_of, from_store
from vibewatt.ingest import antigravity
from vibewatt.sources import ANTIGRAVITY, dedupe

CONVERSATION = "5c0e8a1f-2b7d-4e9a-9c3b-7a1d6f2e4b80"
START = 1791208729  # 2026-10-05T13:58:49Z


# --- a minimal protobuf writer, enough to build the fixture ------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def pb(*fields: tuple[int, int | bytes | str]) -> bytes:
    out = bytearray()
    for number, value in fields:
        if isinstance(value, int):
            out += _varint(number << 3) + _varint(value)
        else:
            raw = value.encode() if isinstance(value, str) else value
            out += _varint(number << 3 | 2) + _varint(len(raw)) + raw
    return bytes(out)


def usage(response_id, *, inp, out, text, thinking, cache=None, model_enum=1318):
    fields = [(1, model_enum), (2, inp), (3, out), (6, 24), (9, text), (10, thinking)]
    if cache is not None:
        fields.insert(3, (5, cache))
    fields.append((11, response_id))
    return pb(*fields)


def generation(block: bytes, model="gemini-3.8-flash-n") -> bytes:
    # A large unrelated config message comes first in real rows; field 1 holds
    # this generation's usage at #4 and its model name at #19.
    return pb(
        (3, pb((1, 1318), (6, 65536))), (1, pb((3, 1318), (4, block), (19, model)))
    )


def step(block: bytes, done: int) -> bytes:
    return pb((1, pb((1, done - 5))), (8, pb((1, done))), (9, block))


def make_db(path: Path, gens: list[bytes], steps: list[bytes], *, workspace=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE gen_metadata (idx integer, data blob, size integer, PRIMARY KEY (idx))"
    )
    conn.execute(
        "CREATE TABLE steps (idx integer, step_type integer, status integer,"
        " metadata blob, PRIMARY KEY (idx))"
    )
    conn.execute(
        "CREATE TABLE trajectory_metadata_blob (id text, data blob, PRIMARY KEY (id))"
    )
    for i, data in enumerate(gens):
        conn.execute("INSERT INTO gen_metadata VALUES (?,?,?)", (i, data, len(data)))
    for i, data in enumerate(steps):
        conn.execute("INSERT INTO steps VALUES (?,?,?,?)", (i, 15, 3, data))
    meta = [(2, pb((1, START))), (6, CONVERSATION)]
    if workspace:
        meta.insert(0, (1, pb((1, "file:///c:/Users/dev/Projects/demo-app"))))
    conn.execute(
        "INSERT INTO trajectory_metadata_blob VALUES ('main', ?)", (pb(*meta),)
    )
    conn.commit()
    conn.close()
    return path


A = usage("LK3Davm0CcCc0-kPs5iQiAs", inp=13557, out=618, text=536, thinking=82)
B = usage(
    "Wq3DaufrHfLV9tMPkNmPmQE", inp=2467, out=127, text=45, thinking=82, cache=16326
)


@pytest.fixture
def agy(tmp_path, monkeypatch):
    home = tmp_path / "gemini"
    db = make_db(
        home / "antigravity" / "conversations" / f"{CONVERSATION}.db",
        [generation(A), generation(B)],
        [step(A, START + 14), step(B, START + 49)],
    )
    monkeypatch.setenv("ANTIGRAVITY_DATA_DIR", str(home / "antigravity"))
    return db


def test_discover_finds_conversation_databases(agy, tmp_path, monkeypatch):
    cli = make_db(
        tmp_path / "gemini" / "antigravity-cli" / "conversations" / "x.db", [], []
    )
    monkeypatch.setenv(
        "ANTIGRAVITY_DATA_DIR",
        os.pathsep.join([str(agy.parent.parent), str(cli.parent.parent)]),
    )
    (agy.parent.parent / "conversation_summaries.db").write_bytes(b"")
    found = set(antigravity.discover())
    assert found == {agy, cli}
    assert (ANTIGRAVITY, agy) in ingest.discover()


def test_each_generation_is_one_turn_with_gemini_semantics(agy):
    turns = sorted(antigravity.parse(agy), key=lambda t: t.ts)
    assert len(turns) == 2
    a, b = turns
    assert a.key == ("agy:LK3Davm0CcCc0-kPs5iQiAs", "")
    assert a.session == CONVERSATION and a.project == "demo-app"
    assert a.model == "gemini-3.8-flash-n"
    # #2 is fresh input, #5 cache read, #3 output including #10 thinking.
    assert (a.input, a.cache_read, a.output, a.thinking) == (13557, 0, 618, 82)
    assert (b.input, b.cache_read, b.output, b.thinking) == (2467, 16326, 127, 82)


def test_time_comes_from_the_step_that_carries_the_same_response(agy):
    a, b = sorted(antigravity.parse(agy), key=lambda t: t.ts)
    assert a.ts == datetime.fromtimestamp(START + 14, UTC)
    assert b.ts == datetime.fromtimestamp(START + 49, UTC)


def test_without_a_step_time_the_conversation_start_is_used(tmp_path):
    db = make_db(tmp_path / "c" / "conversations" / "y.db", [generation(A)], [])
    drops: Counter = Counter()
    [turn] = antigravity.parse(db, drops)
    assert turn.ts == datetime.fromtimestamp(START, UTC)
    assert drops["approximate_time"] == 1


def test_a_backup_copy_counts_once(agy, tmp_path):
    backup = tmp_path / "gemini" / "antigravity-backup" / "conversations"
    backup.mkdir(parents=True)
    shutil.copyfile(agy, backup / agy.name)
    turns = list(antigravity.parse(agy)) + list(antigravity.parse(backup / agy.name))
    kept, dropped = dedupe(turns)
    assert len(kept) == 2 and dropped == 2


def test_response_id_that_parses_as_protobuf_is_still_an_id(tmp_path):
    # Bytes that also form a valid protobuf message. A recursive decoder reads
    # them as a nested message and loses the id; field paths must not.
    tricky = pb((1, 5), (2, 7)).decode("latin-1")
    block = usage(tricky, inp=10, out=3, text=2, thinking=1)
    db = make_db(
        tmp_path / "c" / "conversations" / "z.db",
        [generation(block)],
        [step(block, START + 3)],
    )
    [turn] = antigravity.parse(db)
    assert turn.key[0] == "agy:" + tricky


def test_implausible_counts_are_dropped(tmp_path):
    block = usage("huge", inp=912_685_300, out=3, text=2, thinking=1)
    db = make_db(tmp_path / "c" / "conversations" / "h.db", [generation(block)], [])
    drops: Counter = Counter()
    assert list(antigravity.parse(db, drops)) == []
    assert drops["implausible_count"] == 1


def test_oversized_rows_are_skipped_without_loading_them(agy, monkeypatch):
    monkeypatch.setattr(antigravity, "MAX_ROW_BYTES", 64)
    drops: Counter = Counter()
    assert list(antigravity.parse(agy, drops)) == []
    assert drops["too_large"] == 2


def test_a_database_that_is_not_a_conversation_is_ignored(tmp_path):
    other = tmp_path / "c" / "conversations" / "other.db"
    other.parent.mkdir(parents=True)
    sqlite3.connect(other).close()
    drops: Counter = Counter()
    assert list(antigravity.parse(other, drops)) == []
    assert drops["not_a_conversation"] == 1


def test_gemini_rates_follow_the_promotion_window():
    ts = datetime(2026, 10, 5, tzinfo=UTC)
    assert pricing.rate_for("gemini-3.8-flash", ts=ts) == pricing.Rate(
        0.75, 0.75, 0.75, 0.075, 3.75
    )
    later = datetime(2027, 1, 1, tzinfo=UTC)
    assert pricing.rate_for("gemini-3.8-flash", ts=later) == pricing.Rate(
        1.5, 1.5, 1.5, 0.15, 7.5
    )
    assert (
        pricing.rate_for("gemini-3.8-flash", ts=datetime(2026, 9, 1, tzinfo=UTC))
        is None
    )
    # Antigravity's variant id prices as the API model.
    assert pricing.rate_for("gemini-3.8-flash-n", ts=ts) == pricing.rate_for(
        "gemini-3.8-flash", ts=ts
    )


def test_sync_stores_antigravity_under_its_own_source(agy, tmp_path):
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, cost_of)
        report = from_store(conn, JST, source="antigravity")
        assert report.total.turns == 2
        assert report.total.cost == pytest.approx(
            (13557 + 2467) * 0.75 / 1e6 + 16326 * 0.075 / 1e6 + (618 + 127) * 3.75 / 1e6
        )
        assert "antigravity" not in from_store(conn, JST, source="claude").by_source


def test_a_write_to_the_wal_is_picked_up(agy, tmp_path):
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, cost_of)
        live = sqlite3.connect(agy)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        block = usage("new-response", inp=100, out=10, text=8, thinking=2)
        live.execute("INSERT INTO gen_metadata VALUES (9, ?, 1)", (generation(block),))
        live.commit()
        store.sync_files(conn, ingest.discover(), JST, cost_of)
        live.close()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM turns WHERE source='antigravity'"
            ).fetchone()[0]
            == 3
        )
