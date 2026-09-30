from __future__ import annotations

import shutil
from datetime import timedelta, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
# Fixed +9 rather than ZoneInfo so Windows needs no tzdata; 20:00Z is the next JST day.
JST = timezone(timedelta(hours=9))


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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
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
