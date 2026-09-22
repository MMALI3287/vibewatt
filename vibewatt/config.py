"""User configuration.

Resolution order, first hit wins per key:
  1. command line flags
  2. ./.vibewatt/vibewatt.json      (project local)
  3. $VIBEWATT_CONFIG               (explicit)
  4. <user config dir>/vibewatt/vibewatt.json
  5. built-in defaults

Paths use the platform's real config location so Windows does not get a
dotfile dumped in the user profile root.

The project was called ccburn until 2026-09-22 (renamed because that PyPI name
belongs to an unrelated tool). For one release the old CCBURN_* variables and
ccburn config files are still read, with a deprecation note, and the old data
dir is copied, not moved, so a downgrade still finds its store.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

APP = "vibewatt"
LEGACY_APP = "ccburn"

DEFAULTS: dict[str, Any] = {
    "timezone": "local",
    "weeks": 53,
    "session_length_hours": 5,      # Claude's rate-limit window
    "start_of_week": "monday",
    "include_sidechains": True,
    "offline": False,               # skip the pricing refresh
    "monthly_budget_usd": None,
    "plan_usd_per_month": None,     # e.g. 20 for Pro, 100/200 for Max
    "heatmap_metric": "cost",       # cost | total | output | responses
    "mask_projects": False,
    "pricing_overrides": {},        # {"model-id": {"input": 1.0, "output": 5.0, ...}}
    "project_aliases": {},          # {"-home-user-api": "API"}
    "quota": True,                  # read account-level plan utilization
    "sync_interval_seconds": 60,    # serve: background log sync; 0 disables
    "ai_summary": {
        "enabled": False,
        "model": "claude-haiku-4-5",
        "include_project_names": False,
    },
    "project_paths": {},
}


_warned: set[str] = set()


def deprecated(old: str, new: str) -> None:
    if old in _warned:
        return
    _warned.add(old)
    print(f"{APP}: {old} is deprecated and will stop working next release; use {new}", file=sys.stderr)


def env(name: str) -> str | None:
    """VIBEWATT_<name>, else the pre-rename CCBURN_<name>."""
    value = os.environ.get(f"VIBEWATT_{name}")
    if value:
        return value
    legacy = os.environ.get(f"CCBURN_{name}")
    if legacy:
        deprecated(f"CCBURN_{name}", f"VIBEWATT_{name}")
        return legacy
    return None


def user_config_dir(app: str = APP) -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        return Path(base) / app if base else Path.home() / f".{app}"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / app
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / app


def default_data_dir(app: str = APP) -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / app if base else Path.home() / f".{app}"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / app
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / app


def data_dir() -> Path:
    """Where vibewatt keeps its store, history and caches."""
    override = env("DATA_DIR")
    if override:
        return Path(override).expanduser()
    target = default_data_dir()
    legacy = default_data_dir(LEGACY_APP)
    if not target.exists() and legacy.is_dir():
        # Copy, never move: the old install keeps working until the user removes it.
        shutil.copytree(legacy, target, ignore=shutil.ignore_patterns(f"{LEGACY_APP}.db*"))
        if (legacy / f"{LEGACY_APP}.db").is_file():
            copy_sqlite(legacy / f"{LEGACY_APP}.db", target / f"{APP}.db")
        print(f"{APP}: copied {legacy} to {target}; the old copy is untouched", file=sys.stderr)
    return target


def copy_sqlite(src: Path, dst: Path) -> None:
    # The backup API folds in -wal content that a plain file copy would miss.
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _first_existing(current: Path, legacy: Path) -> Path:
    if current.is_file() or not legacy.is_file():
        return current
    deprecated(str(legacy), str(current))
    return legacy


def load() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    candidates = [
        _first_existing(user_config_dir() / f"{APP}.json", user_config_dir(LEGACY_APP) / f"{LEGACY_APP}.json")
    ]
    explicit = env("CONFIG")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(
        _first_existing(Path.cwd() / f".{APP}" / f"{APP}.json", Path.cwd() / f".{LEGACY_APP}" / f"{LEGACY_APP}.json")
    )
    for path in candidates:
        cfg.update(_read(path))
    return cfg
