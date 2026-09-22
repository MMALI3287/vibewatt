"""Local log ingestion.

Every file-backed source is a module exposing the same two functions:

  discover(cfg) -> list[Path]      files this source owns on this machine
  parse(path)   -> Iterator[Turn]  billable responses in one file, not deduped

Dedup happens across files, not inside a parser, because the same response is
replayed into several files. Cloud sessions have no local file and live in
``ingest.cloud`` with a payload-shaped interface instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from vibewatt.sources import CLAUDE_CODE, COWORK, Turn

from . import claude_code, cowork

SOURCES: dict[str, ModuleType] = {CLAUDE_CODE: claude_code, COWORK: cowork}


def discover(cfg: dict | None = None) -> list[tuple[str, Path]]:
    """Return (source, file) pairs for every session log found on this machine.

    Claude Code on the web and Cowork remote sessions are deliberately absent:
    they run in throwaway cloud containers and never write to this disk. See
    vibewatt.quota for the account-level figures that do include them.
    """
    return [(name, path) for name, mod in SOURCES.items() for path in mod.discover(cfg)]


def parse(source: str, path: Path) -> Iterator[Turn]:
    return SOURCES[source].parse(path)
