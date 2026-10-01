from __future__ import annotations

import sqlite3

import pytest

from vibewatt import store


def test_schema9_upgrade_preserves_synthetic_030_store_and_backup(tmp_path):
    from scripts.check_wheel import check_legacy_upgrade, seed_legacy_store

    path = tmp_path / "vibewatt.db"
    before = seed_legacy_store(path)
    with store.connect(path) as conn:
        assert store.schema_version(conn) == store.SCHEMA_VERSION
    backup = check_legacy_upgrade(path, before)
    with store.connect(path):
        pass
    assert list(tmp_path.glob("vibewatt.db.pre-v*-*.bak")) == [backup]


@pytest.mark.parametrize("target", ["store", "backup"])
def test_upgrade_gate_rejects_lost_usage_in_store_or_backup(tmp_path, target):
    from scripts.check_wheel import check_legacy_upgrade, seed_legacy_store

    path = tmp_path / "vibewatt.db"
    before = seed_legacy_store(path)
    with store.connect(path):
        pass
    victim = (
        path if target == "store" else next(tmp_path.glob("vibewatt.db.pre-v*-*.bak"))
    )
    with sqlite3.connect(victim) as conn:
        conn.execute(
            "UPDATE turns SET input = input + 1 WHERE msg_id = 'legacy-priced'"
        )
    with pytest.raises(AssertionError, match="turns"):
        check_legacy_upgrade(path, before)


def test_legacy_fixture_has_original_schema_without_phase8_columns(tmp_path):
    from scripts.check_wheel import seed_legacy_store

    path = tmp_path / "vibewatt.db"
    seed_legacy_store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone() == (
            "9",
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
        assert not columns & {"web_fetch", "account_id", "machine_id"}
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone() == (2,)
