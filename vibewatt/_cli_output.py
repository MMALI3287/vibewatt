"""Stdout pipe handling shared by module and installed CLI entry points."""

from __future__ import annotations

import errno
import os
import stat
import sys
from typing import NoReturn, TextIO


class StdoutClosed(Exception):
    """Keep stdout closure separate from file and store OSError handlers."""

    def __init__(self, code: int = 1) -> None:
        self.code = code
        super().__init__(code)


class PipeOutput:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def __getattr__(self, name: str):
        return getattr(self.stream, name)

    def _is_closed_pipe(self, exc: OSError) -> bool:
        if isinstance(exc, BrokenPipeError):
            return True
        # Windows' CRT can report EINVAL when the reader of a pipe exits.
        # EINVAL on a regular file or another platform is still a real error.
        if sys.platform == "win32" and exc.errno == errno.EINVAL:
            try:
                mode = os.fstat(self.stream.fileno()).st_mode
            except (AttributeError, OSError):
                return False
            return stat.S_ISFIFO(mode)
        return False

    def _closed(self) -> NoReturn:
        # The interpreter flushes stdout again at shutdown. Redirect its actual
        # descriptor so buffered bytes cannot produce a second broken-pipe error.
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), self.stream.fileno())
        raise StdoutClosed from None

    def write(self, text: str) -> int:
        try:
            return self.stream.write(text)
        except OSError as exc:
            if self._is_closed_pipe(exc):
                self._closed()
            raise

    def flush(self) -> None:
        try:
            self.stream.flush()
        except OSError as exc:
            if self._is_closed_pipe(exc):
                self._closed()
            raise


def print_result(text: str, code: int) -> None:
    """Preserve an already known command failure if its output pipe closes."""
    try:
        print(text)
    except StdoutClosed as exc:
        # An unbuffered write can fail before the command returns its status.
        exc.code = code or 1
        raise
