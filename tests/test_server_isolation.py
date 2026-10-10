"""The e2e and demo servers must never read the developer's real provider data."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _fixture_server():
    spec = importlib.util.spec_from_file_location(
        "fixture_server", ROOT / "web" / "e2e" / "fixture_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _demo_server():
    spec = importlib.util.spec_from_file_location(
        "demo_server", ROOT / "scripts" / "demo_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_isolate_redirects_every_provider_path(tmp_path, monkeypatch):
    from vibewatt import config, store
    from vibewatt.ingest import antigravity, claude_code, codex, copilot

    # Undo the conftest overrides so the paths resolve as on a developer machine.
    for name in (
        "VIBEWATT_COPILOT_CACHE",
        "VIBEWATT_VSCODE_USER_DIRS",
        "CODEX_HOME",
        "ANTIGRAVITY_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    real = tmp_path / "real"
    monkeypatch.setenv("LOCALAPPDATA", str(real / "local"))
    monkeypatch.setattr(
        Path, "home", classmethod(lambda cls: Path(os.path.expanduser("~")))
    )

    sandbox = tmp_path / "sandbox"
    _fixture_server().isolate(sandbox, monkeypatch.setenv)

    paths = [
        copilot.cache_path(),
        *copilot.user_dirs(),
        *antigravity.data_dirs(),
        *codex.roots(),
        *claude_code.roots(),
        config.data_dir(),
        store.db_path(),
    ]
    leaks = [p for p in paths if not p.resolve().is_relative_to(sandbox.resolve())]
    assert not leaks


# --- demo_server port validation (#111) --------------------------------------
# These exercise the parser only: no service is bound and no provider data is
# read, so a bad port is rejected before any temporary directory is created.


def test_demo_server_accepts_a_good_port():
    assert _demo_server().parse_args(["8780"]).port == 8780


def test_demo_server_defaults_to_the_documented_port():
    assert _demo_server().parse_args([]).port == 8779


@pytest.mark.parametrize("bad", ["abc", "0", "-1", "65536", ""])
def test_demo_server_rejects_an_invalid_port(bad):
    with pytest.raises(SystemExit) as exc:
        _demo_server().parse_args([bad])
    assert exc.value.code == 2


def test_demo_server_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        _demo_server().parse_args(["--help"])
    assert exc.value.code == 0
    assert "port" in capsys.readouterr().out
