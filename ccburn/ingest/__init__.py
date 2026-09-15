from __future__ import annotations

from pathlib import Path

from .claude_code import discover as discover_claude_code
from .cowork import discover as discover_cowork
from ccburn.sources import read_file


def discover(cfg: dict | None = None) -> list[tuple[str, Path]]:
    """Return all local log files for Claude Code and Cowork."""
    found: list[tuple[str, Path]] = []
    for path in discover_claude_code(cfg):
        found.append(("claude-code", path))
    for path in discover_cowork(cfg):
        found.append(("cowork", path))
    return found


def parse_file(source: str, path: Path):
    """Parse a single local log file and return the normalized turns, deduped."""
    seen: set[tuple[str, str]] = set()
    turns = []
    for turn in read_file(source, path):
        if turn.key != ("", "") and turn.key in seen:
            continue
        if turn.key != ("", ""):
            seen.add(turn.key)
        turns.append(turn)
    return turns
