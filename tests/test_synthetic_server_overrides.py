"""Inherited synthetic sentinels must not enter demo or browser fixture data."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inherited(tmp_path, monkeypatch):
    external = tmp_path / "external paths"
    external.mkdir()
    monkeypatch.chdir(external)
    monkeypatch.setattr(
        Path, "home", classmethod(lambda cls: Path(os.path.expanduser("~")))
    )
    paths = {
        "HOME": external / "home",
        "USERPROFILE": external / "home",
        "APPDATA": external / "appdata",
        "LOCALAPPDATA": external / "localappdata",
        "XDG_CONFIG_HOME": external / "config",
        "XDG_DATA_HOME": external / "data",
        "CLAUDE_CONFIG_DIR": external / "claude",
        "VIBEWATT_COWORK_DIR": external / "cowork",
        "CCBURN_COWORK_DIR": external / "legacy-cowork",
        "VIBEWATT_DATA_DIR": external / "store",
        "CCBURN_DATA_DIR": external / "legacy-store",
        "CODEX_HOME": external / "codex",
        "VIBEWATT_VSCODE_USER_DIRS": external / "vscode",
        "VIBEWATT_COPILOT_CACHE": external / "cache.json",
        "ANTIGRAVITY_DATA_DIR": external / "antigravity",
    }
    for key, path in paths.items():
        monkeypatch.setenv(key, str(path))
    files = {
        "claude-code": paths["CLAUDE_CONFIG_DIR"] / "projects/demo/session.jsonl",
        "cowork": paths["VIBEWATT_COWORK_DIR"]
        / "local-agent-mode-sessions/a/b/c/audit.jsonl",
        "codex": paths["CODEX_HOME"] / "archived_sessions/rollout-sentinel.jsonl",
        "copilot": paths["VIBEWATT_VSCODE_USER_DIRS"]
        / "workspaceStorage/a/chatSessions/sentinel.jsonl",
        "antigravity": paths["ANTIGRAVITY_DATA_DIR"] / "conversations/sentinel.db",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    paths["VIBEWATT_COPILOT_CACHE"].write_text("{}", encoding="utf-8")
    return external, paths, files


def isolate(path, monkeypatch):
    runpy.run_path(str(ROOT / "web/e2e/fixture_server.py"))["isolate"](
        path, monkeypatch.setenv
    )


@pytest.mark.parametrize(
    "source", ["explicit", "legacy", "project", "legacy-project", "user", "legacy-user"]
)
def test_effective_config_ignores_inherited_files(
    inherited, tmp_path, monkeypatch, source
):
    from vibewatt import config

    external, _, _ = inherited
    if source in ("explicit", "legacy"):
        path = external / "explicit.json"
        monkeypatch.setenv(
            "VIBEWATT_CONFIG" if source == "explicit" else "CCBURN_CONFIG", str(path)
        )
    elif source in ("user", "legacy-user"):
        app = "vibewatt" if source == "user" else "ccburn"
        path = config.user_config_dir(app) / f"{app}.json"
    else:
        app = "vibewatt" if source == "project" else "ccburn"
        path = external / f".{app}" / f"{app}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"isolation_sentinel": source, "project_paths": {"sentinel": str(external)}}
        ),
        encoding="utf-8",
    )
    assert config.load()["isolation_sentinel"] == source
    isolate(tmp_path / "sandbox", monkeypatch)
    assert "isolation_sentinel" not in config.load()
    assert not config.load().get("project_paths")


@pytest.mark.parametrize(
    "provider", ["claude-code", "cowork", "codex", "copilot", "antigravity"]
)
def test_discovery_excludes_external_sentinels(
    inherited, tmp_path, monkeypatch, provider
):
    from vibewatt import ingest

    _, _, files = inherited
    assert files[provider] in ingest.SOURCES[provider].discover()
    isolate(tmp_path / "sandbox", monkeypatch)
    assert files[provider] not in ingest.SOURCES[provider].discover()


@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux"])
def test_all_effective_roots_are_isolated(inherited, tmp_path, monkeypatch, system):
    from vibewatt import config, store
    from vibewatt.ingest import antigravity, claude_code, codex, copilot, cowork

    monkeypatch.setattr("platform.system", lambda: system)
    sandbox = tmp_path / "sandbox"
    isolate(sandbox, monkeypatch)
    paths = [
        Path.cwd(),
        config.user_config_dir(),
        config.user_config_dir("ccburn"),
        config.default_data_dir(),
        config.default_data_dir("ccburn"),
        config.data_dir(),
        store.db_path(),
        copilot.cache_path(),
        *claude_code.roots(),
        *cowork.desktop_data_dirs(),
        *codex.roots(),
        *copilot.user_dirs(),
        *antigravity.data_dirs(),
    ]
    assert all(p.resolve().is_relative_to(sandbox.resolve()) for p in paths)


@pytest.mark.parametrize(
    "launcher", ["scripts/demo_server.py", "web/e2e/fixture_server.py"]
)
def test_launchers_use_isolated_config_and_seed_data(
    inherited, tmp_path, monkeypatch, launcher
):
    external, _, _ = inherited
    config = external / ".vibewatt/vibewatt.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"isolation_sentinel": True, "offline": False, "quota": True}),
        encoding="utf-8",
    )
    explicit = external / "inherited.json"
    explicit.write_text('{"explicit_sentinel": true}', encoding="utf-8")
    monkeypatch.setenv("VIBEWATT_CONFIG", str(explicit))
    # Run the real launcher through seeding and app creation; only replace the
    # blocking server call so the child's effective configuration is observable.
    script = """
import json, runpy, socket, sys, tempfile
from pathlib import Path
import uvicorn

def forbidden(*args, **kwargs):
    raise AssertionError("network forbidden in synthetic launcher regression")
socket.socket.connect = forbidden
socket.getaddrinfo = forbidden
sandbox = Path(sys.argv[2])
def temporary(**kwargs):
    sandbox.mkdir()
    return str(sandbox)
tempfile.mkdtemp = temporary

def inspect(app, **kwargs):
    from vibewatt import store
    from vibewatt.ingest import discover
    cfg = app.state.cfg
    assert "isolation_sentinel" not in cfg and "explicit_sentinel" not in cfg
    assert cfg["offline"] is True and cfg["quota"] is False
    assert store.db_path().resolve().is_relative_to(sandbox)
    assert Path.cwd() == sandbox
    assert all(p.resolve().is_relative_to(sandbox) for _, p in discover(cfg))
    with store.connect() as conn:
        counts = dict(conn.execute("SELECT source, COUNT(*) FROM turns GROUP BY source"))
    assert counts.get("claude-code", 0) > 0
    if "fixture_server" in sys.argv[0]:
        assert counts.get("cowork") == 1
    print(json.dumps({"offline": cfg["offline"], "quota": cfg["quota"], "counts": counts}))
uvicorn.run = inspect
launcher = sys.argv[1]
sys.argv = [launcher]
runpy.run_path(launcher, run_name="__main__")
"""
    env = dict(os.environ)
    # Do not inherit any parent sitecustomize/Path.home test bootstrap.
    env.pop("PYTHONPATH", None)
    before = dict(os.environ)
    cwd = Path.cwd()
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT / launcher), str(tmp_path / "child")],
        cwd=external,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert os.environ == before and Path.cwd() == cwd
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["offline"] is True


@pytest.mark.parametrize(
    "key",
    [
        "CLAUDE_CONFIG_DIR",
        "VIBEWATT_COWORK_DIR",
        "VIBEWATT_VSCODE_USER_DIRS",
        "ANTIGRAVITY_DATA_DIR",
    ],
)
def test_inherited_path_lists_are_replaced(inherited, tmp_path, monkeypatch, key):
    from vibewatt import ingest

    _, paths, files = inherited
    monkeypatch.setenv(
        key, os.pathsep.join([str(paths[key]), str(tmp_path / "other path")])
    )
    before = {p for _, p in ingest.discover()}
    assert set(files.values()) <= before
    isolate(tmp_path / "sandbox", monkeypatch)
    assert not (set(files.values()) & {p for _, p in ingest.discover()})


def test_legacy_roots_and_relative_sandbox(inherited, tmp_path, monkeypatch):
    from vibewatt import config, store
    from vibewatt.ingest import cowork

    _, paths, _ = inherited
    monkeypatch.delenv("VIBEWATT_COWORK_DIR")
    monkeypatch.delenv("VIBEWATT_DATA_DIR")
    legacy = paths["CCBURN_COWORK_DIR"] / "claude-code-sessions/a/audit.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}\n", encoding="utf-8")
    assert legacy in cowork.discover()
    assert config.data_dir() == paths["CCBURN_DATA_DIR"]
    sandbox = Path.cwd() / "relative sandbox"
    isolate(Path("relative sandbox"), monkeypatch)
    assert Path.cwd() == sandbox
    assert legacy not in cowork.discover()
    assert store.db_path().is_relative_to(sandbox)
    # Empty current overrides must not expose inherited legacy paths again.
    monkeypatch.setenv("VIBEWATT_DATA_DIR", "")
    monkeypatch.setenv("VIBEWATT_COWORK_DIR", "")
    assert config.data_dir().is_relative_to(sandbox)
    assert all(p.is_relative_to(sandbox) for p in cowork.desktop_data_dirs())
