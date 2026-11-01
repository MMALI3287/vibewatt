"""Atomic UTF-8 output for CLI report and agent snapshot files."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> None:
    """Replace one output file only after fully writing its sibling temp file.

    The caller creates parent directories. Replacements do not follow destination
    symlinks. Existing regular-file permissions are preserved when possible;
    new files use the secure 0600 permissions supplied by mkstemp.
    """
    destination = Path(path)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        try:
            prior = os.stat(destination, follow_symlinks=False)
        except FileNotFoundError:
            prior = None
        if prior is not None and stat.S_ISREG(prior.st_mode):
            os.chmod(temporary, stat.S_IMODE(prior.st_mode))

        encoded = text.encode("utf-8")
        offset = 0
        while offset < len(encoded):
            written = os.write(fd, encoded[offset:])
            if written <= 0:
                raise OSError("Atomic output write made no progress")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, destination)
    finally:
        if fd >= 0:
            os.close(fd)
        Path(temporary).unlink(missing_ok=True)
