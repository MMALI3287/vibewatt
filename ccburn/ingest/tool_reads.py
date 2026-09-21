"""Read-tool metadata only. Never retain file contents or literal paths."""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
from collections.abc import Iterator
from pathlib import Path

from ..sources import _parse_ts, _project_name


def read_tools(source: str, path: Path) -> Iterator[dict]:
    """Keep each tool invocation, not each repeated usage/content record."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            raw_stamp = rec.get("timestamp") or rec.get("_audit_timestamp")
            stamp = _parse_ts(raw_stamp) if isinstance(raw_stamp, str) else None
            if not isinstance(msg, dict) or stamp is None:
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            session = str(rec.get("sessionId") or rec.get("session_id") or path.stem)
            cwd = rec.get("cwd") if isinstance(rec.get("cwd"), str) else ""
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Read" or not isinstance(block.get("id"), str):
                    continue
                args = block.get("input")
                file_path = args.get("file_path") if isinstance(args, dict) else None
                if not isinstance(file_path, str) or not file_path or not block["id"]:
                    continue
                # Normalize using the producer's path syntax, not this host's OS.
                windows = (
                    "\\" in file_path + cwd
                    or ntpath.splitdrive(file_path)[0]
                    or ntpath.splitdrive(cwd)[0]
                )
                paths = ntpath if windows else posixpath
                canonical = paths.normpath(paths.join(cwd, file_path))
                if paths is ntpath:
                    canonical = paths.normcase(canonical)
                digest = hashlib.sha256(f"{session}\0{canonical}".encode()).hexdigest()
                yield {
                    "session": session,
                    "tool_id": block["id"],
                    "ts": stamp.isoformat(),
                    "source": source,
                    "project": _project_name(source, path, cwd),
                    "model": str(msg.get("model") or ""),
                    "path_hash": digest,
                }
