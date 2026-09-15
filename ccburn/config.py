"""User configuration.

Resolution order, first hit wins per key:
  1. command line flags
  2. ./.ccburn/ccburn.json          (project local)
  3. $CCBURN_CONFIG                 (explicit)
  4. <user config dir>/ccburn/ccburn.json
  5. built-in defaults

Paths use the platform's real config location so Windows does not get a
dotfile dumped in the user profile root.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "timezone": "local",
    "weeks": 53,
    "session_length_hours": 5,      # Claude's rate-limit window
    "start_of_week": "monday",
    "include_sidechains": True,
    "offline": False,               # skip the pricing refresh
    "monthly_budget_usd": None,
    "mask_projects": False,
    "pricing_overrides": {},        # {"model-id": {"input": 1.0, "output": 5.0, ...}}
    "project_aliases": {},          # {"-home-user-api": "API"}
    "quota": True,                  # read account-level plan utilization
    "history": True,                # persist rollups so pruning cannot erase them
}


def user_config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        return Path(base) / "ccburn" if base else Path.home() / ".ccburn"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ccburn"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "ccburn"


def data_dir() -> Path:
    """Where ccburn keeps its own history and caches."""
    override = os.environ.get("CCBURN_DATA_DIR")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "ccburn" if base else Path.home() / ".ccburn"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ccburn"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "ccburn"


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    candidates = [user_config_dir() / "ccburn.json"]
    explicit = os.environ.get("CCBURN_CONFIG")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(Path.cwd() / ".ccburn" / "ccburn.json")
    for path in candidates:
        cfg.update(_read(path))
    return cfg
