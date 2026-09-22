"""Claude Code: ~/.claude/projects/**/*.jsonl"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from vibewatt.sources import CLAUDE_CODE, Turn, read_file


def roots() -> list[Path]:
    """Honour CLAUDE_CONFIG_DIR, which may hold several path-separated entries."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    bases = (
        [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
        if configured
        else [Path.home() / ".claude"]
    )
    return [b / "projects" for b in bases]


def discover(cfg: dict | None = None) -> list[Path]:
    files: list[Path] = []
    for root in roots():
        if root.is_dir():
            files.extend(sorted(root.rglob("*.jsonl")))
    return files


def parse(path: Path) -> Iterator[Turn]:
    return read_file(CLAUDE_CODE, path)
