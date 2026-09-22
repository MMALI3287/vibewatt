"""Cowork: <desktop data dir>/{local-agent-mode-sessions,claude-code-sessions}/**/audit.jsonl"""

from __future__ import annotations

import os
import platform
from collections.abc import Iterator
from pathlib import Path

from vibewatt.config import env
from vibewatt.sources import COWORK, Turn, read_file

_COWORK_DIRS = ("local-agent-mode-sessions", "claude-code-sessions")


def desktop_data_dirs() -> list[Path]:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        roots = [home / "Library" / "Application Support" / "Claude"]
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        roots = [Path(appdata) / "Claude"] if appdata else []
    else:
        cfg = os.environ.get("XDG_CONFIG_HOME") or (home / ".config")
        roots = [Path(cfg) / "Claude"]
    extra = env("COWORK_DIR")
    if extra:
        roots = [Path(p).expanduser() for p in extra.split(os.pathsep)] + roots
    return roots


def discover(cfg: dict | None = None) -> list[Path]:
    files: list[Path] = []
    for base in desktop_data_dirs():
        for name in _COWORK_DIRS:
            root = base / name
            if root.is_dir():
                files.extend(sorted(root.rglob("audit.jsonl")))
    return files


def parse(path: Path) -> Iterator[Turn]:
    return read_file(COWORK, path)
