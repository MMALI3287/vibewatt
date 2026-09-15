from __future__ import annotations

import os
from pathlib import Path

from ccburn.sources import read_file


def discover(cfg: dict | None = None) -> list[Path]:
    """Return every Claude Code JSONL file for this machine."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    roots = (
        [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
        if configured
        else [Path.home() / ".claude"]
    )
    files: list[Path] = []
    for root in roots:
        project_root = root / "projects"
        if project_root.is_dir():
            files.extend(sorted(project_root.rglob("*.jsonl")))
    return files


def parse(path: Path):
    """Parse one Claude Code log file into normalized turns."""
    return list(read_file("claude-code", path))
