from __future__ import annotations

import shutil
import sqlite3
from datetime import timedelta, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
# Fixed +9 rather than ZoneInfo so Windows needs no tzdata; 20:00Z is the next JST day.
JST = timezone(timedelta(hours=9))


@pytest.fixture
def future_store(tmp_path):
    from vibewatt import store

    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta VALUES ('schema', ?)", (str(store.SCHEMA_VERSION + 1),)
        )
        conn.execute("CREATE TABLE future_usage (value TEXT)")
        conn.execute("INSERT INTO future_usage VALUES ('synthetic usage')")
    conn.close()
    return path


@pytest.fixture(autouse=True)
def loopback_test_client(monkeypatch):
    """TestClient sends Host: testserver, which the Host allowlist rejects."""
    from starlette.testclient import TestClient

    original = TestClient.__init__

    def init(self, app, base_url="http://127.0.0.1:8777", **kw):
        original(self, app, base_url=base_url, **kw)

    monkeypatch.setattr(TestClient, "__init__", init)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """No test may read the developer's real ~/.claude or write their real store."""
    from vibewatt import pricing

    # Remote rate history is process state; one test's refresh must not price another's.
    monkeypatch.setattr(pricing, "_history", {})
    monkeypatch.setattr(pricing, "_windows", {})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("VIBEWATT_VSCODE_USER_DIRS", str(tmp_path / "vscode-user"))
    monkeypatch.setenv("VIBEWATT_COPILOT_CACHE", str(tmp_path / "copilot-cache.json"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    # Pre-rename names left in a developer shell must not leak into a test.
    for name in (
        "VIBEWATT_COWORK_DIR",
        "VIBEWATT_CONFIG",
        "CCBURN_COWORK_DIR",
        "CCBURN_CONFIG",
        "CCBURN_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIBEWATT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """Lay the fixtures out where each source's discover() looks."""
    # A separate override root works on macOS without duplicating native discovery elsewhere.
    monkeypatch.setenv("VIBEWATT_COWORK_DIR", str(tmp_path / "cowork"))
    cc = tmp_path / "claude" / "projects" / "demo" / "session.jsonl"
    cw = (
        tmp_path
        / "cowork"
        / "local-agent-mode-sessions"
        / "acct"
        / "space"
        / "id"
        / "audit.jsonl"
    )
    for src, dst in (
        (FIXTURES / "claude_code_session.jsonl", cc),
        (FIXTURES / "cowork_audit.jsonl", cw),
    ):
        dst.parent.mkdir(parents=True)
        shutil.copyfile(src, dst)
    return {"claude-code": cc, "cowork": cw}
