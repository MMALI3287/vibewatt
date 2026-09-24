"""Local log ingestion.

Every file-backed source is a module exposing the same two functions:

  discover(cfg) -> list[Path]      files this source owns on this machine
  parse(path)   -> Iterator[Turn]  billable responses in one file, not deduped

Dedup happens across files, not inside a parser, because the same response is
replayed into several files. Cloud sessions have no local file and live in
``ingest.cloud`` with a payload-shaped interface instead.
"""

from __future__ import annotations

import fnmatch
import os
from collections import Counter
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


def parse(
    source: str, path: Path, drops: Counter | None = None, raw: dict | None = None
) -> Iterator[Turn]:
    return SOURCES[source].parse(path, drops, raw)


def walk(root: Path, pattern: str) -> list[Path]:
    """Files under root matching pattern, each real file once.

    Directory symlinks and NTFS junctions are not followed: a junction loop
    listed one file 64 times and each copy was parsed (A-070).
    """
    isjunction = getattr(os.path, "isjunction", lambda _p: False)
    found: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not (
                os.path.islink(os.path.join(dirpath, d))
                or isjunction(os.path.join(dirpath, d))
            )
        )
        for name in sorted(filenames):
            if fnmatch.fnmatch(name, pattern):
                path = Path(dirpath) / name
                found.setdefault(os.path.normcase(os.path.realpath(path)), path)
    return list(found.values())
