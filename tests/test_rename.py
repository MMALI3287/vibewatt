"""The ccburn -> vibewatt rename must not strand an existing install."""

from __future__ import annotations

import json
import platform
import sqlite3

from vibewatt import config, store


def _legacy_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('from-ccburn')")
    conn.commit()
    conn.close()


def _marker(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT v FROM marker").fetchone()[0]
    finally:
        conn.close()


def _use_default_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBEWATT_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "local"))
    config._warned.clear()


def test_default_legacy_store_is_copied(tmp_path, monkeypatch, capsys):
    _use_default_data_dir(tmp_path, monkeypatch)
    legacy_dir = config.default_data_dir("ccburn")
    _legacy_db(legacy_dir / "ccburn.db")
    (legacy_dir / "history.json").write_text("{}", encoding="utf-8")

    path = store.db_path()

    assert path == config.default_data_dir() / "vibewatt.db"
    assert _marker(path) == "from-ccburn"
    assert (path.parent / "history.json").is_file()
    assert not (path.parent / "ccburn.db").exists(), "no dead copy of the old store"
    assert (legacy_dir / "ccburn.db").is_file(), (
        "the old store must stay for a downgrade"
    )
    assert "copied" in capsys.readouterr().err


def test_legacy_env_data_dir_is_honoured(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("VIBEWATT_DATA_DIR", raising=False)
    monkeypatch.setenv("CCBURN_DATA_DIR", str(tmp_path / "custom"))
    config._warned.clear()
    _legacy_db(tmp_path / "custom" / "ccburn.db")

    path = store.db_path()

    assert path == tmp_path / "custom" / "vibewatt.db"
    assert _marker(path) == "from-ccburn"
    assert "CCBURN_DATA_DIR is deprecated" in capsys.readouterr().err


def test_new_env_wins_over_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("CCBURN_DATA_DIR", str(tmp_path / "old"))
    monkeypatch.setenv("VIBEWATT_DATA_DIR", str(tmp_path / "new"))
    assert config.data_dir() == tmp_path / "new"


def test_existing_new_store_is_not_overwritten(tmp_path, monkeypatch):
    _use_default_data_dir(tmp_path, monkeypatch)
    _legacy_db(config.default_data_dir("ccburn") / "ccburn.db")
    new_dir = config.default_data_dir()
    new_dir.mkdir(parents=True)
    conn = sqlite3.connect(new_dir / "vibewatt.db")
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('already-vibewatt')")
    conn.commit()
    conn.close()

    assert _marker(store.db_path()) == "already-vibewatt"


def test_legacy_config_file_is_read(tmp_path, monkeypatch, capsys):
    config._warned.clear()
    legacy = config.user_config_dir("ccburn") / "ccburn.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"weeks": 12}), encoding="utf-8")

    assert config.load()["weeks"] == 12
    assert "deprecated" in capsys.readouterr().err

    current = config.user_config_dir() / "vibewatt.json"
    current.parent.mkdir(parents=True)
    current.write_text(json.dumps({"weeks": 30}), encoding="utf-8")
    assert config.load()["weeks"] == 30


def test_legacy_cowork_env_is_read(tmp_path, monkeypatch):
    from vibewatt.ingest.cowork import desktop_data_dirs

    config._warned.clear()
    monkeypatch.setenv("CCBURN_COWORK_DIR", str(tmp_path / "cw"))
    assert desktop_data_dirs()[0] == tmp_path / "cw"


def test_platform_dirs_use_new_name():
    assert config.user_config_dir().name == "vibewatt"
    if platform.system() != "Darwin":
        assert config.default_data_dir().name == "vibewatt"
