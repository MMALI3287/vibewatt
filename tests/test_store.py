from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest
from conftest import JST

from vibewatt import ingest, quota, store


def _cost(_turn) -> float:
    return 1.0


def _turn_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]


def test_fresh_store_is_at_current_schema(tmp_path):
    with store.connect(tmp_path / "db.sqlite") as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert store.schema_version(conn) == store.SCHEMA_VERSION == 13
    assert {
        "meta",
        "turns",
        "sessions",
        "titles",
        "files",
        "quota_samples",
        "findings",
        "tool_reads",
        "tool_read_files",
    } <= tables


def test_future_store_is_rejected_without_writes_or_backup(future_store, monkeypatch):
    before = future_store.read_bytes()
    files_before = set(future_store.parent.iterdir())
    entered = False

    def forbidden(*args, **kwargs):
        raise AssertionError("must not migrate an unsupported schema")

    monkeypatch.setattr(store, "migrate", forbidden)
    with (
        pytest.raises(ValueError, match="newer than supported schema"),
        store.connect(future_store) as conn,
    ):
        entered = True
        conn.execute("UPDATE future_usage SET value = 'overwritten'")

    assert not entered
    assert future_store.read_bytes() == before
    assert set(future_store.parent.iterdir()) == files_before


def test_current_store_remains_writable_without_migration_backup(tmp_path):
    path = tmp_path / "current.db"
    with store.connect(path) as conn:
        conn.execute("INSERT INTO meta VALUES ('sentinel', 'original')")
    with store.connect(path) as conn:
        conn.execute("UPDATE meta SET value = 'updated' WHERE key = 'sentinel'")
    with store.connect(path) as conn:
        assert (
            conn.execute("SELECT value FROM meta WHERE key = 'sentinel'").fetchone()[0]
            == "updated"
        )
    assert not list(tmp_path.glob("*.bak"))


def test_v1_store_migrates_forward_without_losing_rows(tmp_path):
    path = tmp_path / "db.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(store.SCHEMA)
    raw.execute("INSERT INTO meta VALUES ('schema', '1')")
    raw.execute(
        "CREATE TABLE prompts (session TEXT NOT NULL, ts TEXT, text TEXT NOT NULL, PRIMARY KEY (session, text))"
    )  # pre-schema-6 table
    raw.execute("INSERT INTO prompts VALUES ('s', NULL, 'kept')")
    raw.commit()
    raw.close()

    with store.connect(path) as conn:
        assert store.schema_version(conn) == store.SCHEMA_VERSION
        assert conn.execute("SELECT text FROM titles").fetchone()[0] == "kept"
        conn.execute("INSERT INTO files VALUES ('p', 1.0, 1, 't', 0, NULL)")
        conn.execute(
            "INSERT INTO quota_samples VALUES ('t', 'five_hour', '5-hour', 'account', 1.0, NULL, 'x')"
        )


def test_sync_skips_unchanged_files(tmp_path, logs):
    files = ingest.discover()
    with store.connect(tmp_path / "db.sqlite") as conn:
        first = store.sync_files(conn, files, JST, _cost)
        count = _turn_count(conn)
        second = store.sync_files(conn, files, JST, _cost)
        assert _turn_count(conn) == count == 3

    assert (first.parsed, first.skipped, first.turns, first.duplicates) == (2, 0, 3, 1)
    assert (second.parsed, second.skipped, second.turns) == (0, 2, 0)


def test_sync_reparses_a_changed_file(tmp_path, logs):
    files = ingest.discover()
    path = logs["claude-code"]
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, files, JST, _cost)
        new_line = (
            path.read_text(encoding="utf-8").splitlines()[2].replace('"m2"', '"m3"')
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(new_line + "\n")
        result = store.sync_files(conn, files, JST, _cost)
        recorded = conn.execute(
            "SELECT turn_count FROM files WHERE path = ?", (str(path),)
        ).fetchone()[0]
        assert _turn_count(conn) == 4

    assert (result.parsed, result.skipped) == (1, 1)
    assert (
        recorded == 3
    )  # responses in the file (A-114), not the lines that repeat them


def test_response_replayed_across_files_counts_once(tmp_path, logs):
    shutil.copyfile(logs["claude-code"], logs["claude-code"].with_name("resumed.jsonl"))
    with store.connect(tmp_path / "db.sqlite") as conn:
        result = store.sync_files(conn, ingest.discover(), JST, _cost)
        assert _turn_count(conn) == 3

    assert result.duplicates == 4  # six Claude Code lines, two unique responses


def test_sync_buckets_days_in_the_report_timezone(tmp_path, logs):
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, _cost)
        day = conn.execute("SELECT day FROM turns WHERE msg_id = 'm1'").fetchone()[0]
    assert day == "2026-09-16"  # 20:00Z on the 15th is the 16th in JST


def test_sync_records_prompts(tmp_path, logs):
    with store.connect(tmp_path / "db.sqlite") as conn:
        result = store.sync_files(conn, ingest.discover(), JST, _cost)
        texts = [r[0] for r in conn.execute("SELECT text FROM titles")]
    assert result.prompts >= 1
    assert "fix the sync" in texts


def test_sync_survives_a_file_vanishing(tmp_path, logs):
    files = ingest.discover()
    os.remove(logs["cowork"])
    with store.connect(tmp_path / "db.sqlite") as conn:
        result = store.sync_files(conn, files, JST, _cost)
    assert (result.parsed, result.turns) == (1, 2)


def _quota(when: datetime) -> quota.Quota:
    windows = [
        quota.Window(
            key="five_hour",
            utilization=60.0,
            resets_at=when + timedelta(hours=1),
        ),
        quota.Window(key="seven_day", utilization=40.0, resets_at=None),
    ]
    return quota.Quota(windows, "statusline", when)


def test_same_quota_reading_is_stored_once(tmp_path):
    reading = _quota(datetime(2026, 9, 15, 11, tzinfo=UTC))
    later = _quota(datetime(2026, 9, 15, 11, 5, tzinfo=UTC))
    with store.connect(tmp_path / "db.sqlite") as conn:
        assert quota.record(conn, reading) == 2
        assert quota.record(conn, reading) == 0
        assert quota.record(conn, later) == 2
        assert conn.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0] == 4


def test_quota_read_persists_samples(monkeypatch):
    reading = _quota(datetime.now(UTC))
    monkeypatch.setattr(quota, "read_token", lambda: "token")
    monkeypatch.setattr(quota, "fetch", lambda *a, **k: reading)

    q, note = quota.read({"quota": True})

    assert note == ""
    assert [(w.key, w.utilization) for w in q.windows] == [
        ("five_hour", 60.0),
        ("seven_day", 40.0),
    ]
    with store.connect() as conn:
        labels = {r[0] for r in conn.execute("SELECT label FROM quota_samples")}
    assert labels == {"5-hour", "7-day"}


def test_quota_read_survives_a_broken_store(monkeypatch):
    reading = _quota(datetime.now(UTC))
    monkeypatch.setattr(quota, "read_token", lambda: "token")
    monkeypatch.setattr(quota, "fetch", lambda *a, **k: reading)

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "connect", locked)
    assert quota.read({"quota": True}) == (reading, "")


def test_store_from_first_phase1_attempt_gets_quota_key_and_resync(tmp_path):
    # b80b95e stamped schema 2 with an unkeyed quota_samples table and files rows
    # whose prompts were never recorded.
    path = tmp_path / "db.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(store.SCHEMA)
    raw.executescript(
        "CREATE TABLE files (path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL,"
        " parsed_at TEXT NOT NULL, turn_count INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE quota_samples (ts TEXT NOT NULL, label TEXT NOT NULL,"
        " utilization REAL NOT NULL, resets_at TEXT);"
        "INSERT INTO meta VALUES ('schema', '2');"
        "INSERT INTO files VALUES ('x', 1.0, 1, 't', 1);"
        "INSERT INTO quota_samples VALUES ('t', '5-hour', 1.0, NULL);"
        "INSERT INTO quota_samples VALUES ('t', '5-hour', 1.0, NULL);"
    )
    raw.commit()
    raw.close()

    reading = _quota(datetime(2026, 9, 15, 11, tzinfo=UTC))
    with store.connect(path) as conn:
        assert store.schema_version(conn) == store.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        quota.record(conn, reading)
        assert quota.record(conn, reading) == 0


def test_changing_timezone_rebuckets_unchanged_files(tmp_path, logs):
    files = ingest.discover()
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, files, UTC, _cost)
        utc_day = conn.execute("SELECT day FROM turns WHERE msg_id = 'm1'").fetchone()[
            0
        ]
        result = store.sync_files(conn, files, JST, _cost)
        jst_day = conn.execute("SELECT day FROM turns WHERE msg_id = 'm1'").fetchone()[
            0
        ]
        again = store.sync_files(conn, files, JST, _cost)

    assert (utc_day, jst_day) == ("2026-09-15", "2026-09-16")
    assert result.parsed == 0  # rebucketed in place, no file re-read (A-113)
    assert again.parsed == 0


def test_statusline_without_captured_at_is_one_sample(tmp_path, monkeypatch):
    import json

    dump = tmp_path / "statusline.json"
    dump.write_text(
        json.dumps({"rate_limits": {"five_hour": {"used_percentage": 42}}}),
        encoding="utf-8",
    )
    assert quota.from_statusline(dump) is not None
    for _ in range(3):
        quota.read({"quota": True, "statusline_cache_path": str(dump)})
    with store.connect() as conn:
        assert (
            conn.execute("SELECT COUNT(DISTINCT ts) FROM quota_samples").fetchone()[0]
            == 1
        )


def test_timezone_change_rebuckets_turns_whose_file_is_gone(tmp_path, logs):
    # History outlives pruned transcripts, so re-parsing alone cannot fix its days.
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), UTC, _cost)
        os.remove(logs["claude-code"])
        store.sync_files(conn, ingest.discover(), JST, _cost)
        day = conn.execute("SELECT day FROM turns WHERE msg_id = 'm1'").fetchone()[0]
    assert day == "2026-09-16"


def test_zones_sharing_a_name_but_not_an_offset_are_different(tmp_path, logs):
    from datetime import timedelta

    us_central = timezone(timedelta(hours=-6), "CST")
    china = timezone(timedelta(hours=8), "CST")
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), us_central, _cost)
        store.sync_files(conn, ingest.discover(), china, _cost)
        day = conn.execute("SELECT day FROM turns WHERE msg_id = 'm1'").fetchone()[0]
    assert day == "2026-09-16"


def test_interrupted_v3_migration_recovers_stranded_samples(tmp_path):
    # Crash after the keyed table was created but before the old one was copied.
    path = tmp_path / "db.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(store.SCHEMA)
    store._v2_files_and_quota_samples(raw)  # the keyed schema-2 table, as v3 created it
    raw.executescript(
        "INSERT INTO meta VALUES ('schema', '2');"
        "CREATE TABLE quota_samples_unkeyed (ts TEXT, label TEXT, utilization REAL, resets_at TEXT);"
        "INSERT INTO quota_samples_unkeyed VALUES ('t', '5-hour', 1.0, NULL);"
    )
    raw.commit()
    raw.close()
    with store.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0] == 1
        gone = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'quota_samples_unkeyed'"
        ).fetchone()[0]
    assert gone == 0
