from __future__ import annotations

import shutil
from datetime import timedelta, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
# Fixed +9 rather than ZoneInfo so Windows needs no tzdata; 20:00Z is the next JST day.
JST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """No test may read the developer's real ~/.claude or write their real store."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    monkeypatch.delenv("CCBURN_COWORK_DIR", raising=False)
    monkeypatch.setenv("CCBURN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture
def logs(tmp_path):
    """Lay the fixtures out where each source's discover() looks."""
    cc = tmp_path / "claude" / "projects" / "demo" / "session.jsonl"
    cw = (
        tmp_path
        / "appdata"
        / "Claude"
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
