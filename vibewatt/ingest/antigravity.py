"""Google Antigravity: ~/.gemini/antigravity{,-cli,-ide,-backup}/conversations/*.db

Each conversation is a SQLite database. `gen_metadata` holds one protobuf
message per model generation and `steps` holds the agent steps, a planner step
carrying the same usage block with its timestamps. There is no published
schema: the field numbers below were matched against peer parsers and checked
on real data (docs/DATA-SOURCES.md, "Antigravity").
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from vibewatt.sources import ANTIGRAVITY, Turn

_DIRS = ("antigravity", "antigravity-cli", "antigravity-ide", "antigravity-backup")
# Later rows can carry large embedded histories; a row past this is skipped
# rather than loaded. No local row has exceeded 300 KB.
MAX_ROW_BYTES = 64 * 1024 * 1024
# A count above this is a misread field, not usage (a nanosecond counter once
# passed for an output count in a peer parser).
MAX_TOKENS = 2_000_000


def data_dirs() -> list[Path]:
    """Folders holding `conversations/`. ANTIGRAVITY_DATA_DIR overrides them."""
    configured = os.environ.get("ANTIGRAVITY_DATA_DIR")
    if configured:
        return [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
    gemini = Path.home() / ".gemini"
    return [gemini / name for name in _DIRS] + [Path.home() / ".config" / "antigravity"]


def discover(cfg: dict | None = None) -> list[Path]:
    files: list[Path] = []
    for base in data_dirs():
        folder = base / "conversations"
        if folder.is_dir():
            files.extend(sorted(p for p in folder.glob("*.db") if p.is_file()))
    return files


def change_marker(path: Path) -> tuple[float, int]:
    """(mtime, size) that also moves when only the write-ahead log grew."""
    st = path.stat()
    mtime, size = st.st_mtime, st.st_size
    try:
        wal = Path(f"{path}-wal").stat()
    except OSError:
        return mtime, size
    return max(mtime, wal.st_mtime), size + wal.st_size


# --- protobuf, one level at a time --------------------------------------------


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("truncated varint")
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def _fields(buf: bytes | None) -> dict[int, list]:
    """One message level as {field: [int or bytes]}. Never recurses.

    Reading nested messages only by known field paths keeps a string that
    happens to be valid protobuf (a response id) from being read as a message.
    """
    out: dict[int, list] = {}
    if not buf:
        return out
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            length, i = _varint(buf, i)
            if i + length > len(buf):
                raise ValueError("truncated field")
            value = buf[i : i + length]
            i += length
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(number, []).append(value)
    return out


def _first(message: dict[int, list], number: int, kind: type):
    for value in message.get(number, []):
        if isinstance(value, kind):
            return value
    return None


def _text(message: dict[int, list], number: int) -> str | None:
    raw = _first(message, number, bytes)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _times(conn) -> dict[str, int]:
    """Response id -> completion time (epoch seconds) from planner steps."""
    times: dict[str, int] = {}
    for (meta,) in conn.execute(
        "SELECT metadata FROM steps WHERE metadata IS NOT NULL"
    ):
        try:
            step = _fields(meta)
            block = _fields(_first(step, 9, bytes))
        except ValueError:
            continue
        response = _text(block, 11)
        if not response:
            continue
        # #8, #7 and #6 are successive step times; the last one set wins.
        for number in (8, 7, 6):
            stamp = _first(_fields(_first(step, number, bytes)), 1, int)
            if stamp:
                times[response] = stamp
                break
    return times


def _conversation(conn, path: Path) -> tuple[str, str, int | None]:
    row = conn.execute("SELECT data FROM trajectory_metadata_blob LIMIT 1").fetchone()
    meta = _fields(row[0]) if row and row[0] else {}
    session = _text(meta, 6) or path.stem
    uri = _text(_fields(_first(meta, 1, bytes)), 1) or ""
    project = Path(unquote(urlparse(uri).path).rstrip("/")).name or "antigravity"
    start = _first(_fields(_first(meta, 2, bytes)), 1, int)
    return session, project, start


def parse(
    path: Path, drops: Counter | None = None, raw: dict | None = None
) -> Iterator[Turn]:
    """One Turn per generation with usage, not deduped.

    Usage is `gen_metadata.data` #1.#4: #2 fresh input, #5 cache read, #3 output
    (visible #9 plus thinking #10), #11 response id. The model name is #1.#19.
    The database is opened read-only, so a running Antigravity is undisturbed.
    """
    drops = drops if drops is not None else Counter()
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise OSError(str(exc)) from exc
    with closing(conn):
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise OSError(str(exc)) from exc
        if "gen_metadata" not in tables:
            drops["not_a_conversation"] += 1
            return
        session, project, start = (
            _conversation(conn, path)
            if "trajectory_metadata_blob" in tables
            else (path.stem, "antigravity", None)
        )
        times = _times(conn) if "steps" in tables else {}
        rows = conn.execute(
            "SELECT idx, CASE WHEN length(data) <= ? THEN data END, length(data)"
            " FROM gen_metadata ORDER BY idx",
            (MAX_ROW_BYTES,),
        )
        for idx, data, size in rows:
            if data is None:
                drops["too_large" if size else "no_usage"] += 1
                continue
            try:
                generation = _fields(_first(_fields(data), 1, bytes))
                block = _fields(_first(generation, 4, bytes))
            except ValueError:
                drops["bad_protobuf"] += 1
                continue
            counts = {n: _first(block, n, int) or 0 for n in (2, 3, 5, 10)}
            if not counts[2] and not counts[3]:
                drops["no_usage"] += 1
                continue
            if any(v > MAX_TOKENS for v in counts.values()) or counts[10] > counts[3]:
                drops["implausible_count"] += 1
                continue
            model = _text(generation, 19)
            if not model:
                drops["no_model"] += 1
                continue
            response = _text(block, 11)
            stamp = times.get(response) if response else None
            if stamp is None:
                stamp = start
                drops["approximate_time"] += 1
            if not stamp:
                drops["bad_timestamp"] += 1
                continue
            yield Turn(
                source=ANTIGRAVITY,
                ts=datetime.fromtimestamp(stamp, UTC),
                model=model,
                input=counts[2],
                cache_5m=0,
                cache_1h=0,
                cache_read=counts[5],
                output=counts[3],
                thinking=counts[10],
                web_searches=0,
                fast=False,
                geo=None,
                sidechain=False,
                project=project,
                session=session,
                key=(f"agy:{response}", "")
                if response
                else (f"agy:{session}", f"gen{idx}"),
            )
