"""Bounded, versioned transfer of response inputs between local stores."""

from __future__ import annotations

import gzip
import json
import math
import os
import sqlite3
import tempfile
from dataclasses import asdict, fields, replace
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from uuid import UUID

from . import identity, store
from .aggregate import cost_of
from .sources import CLAUDE_SOURCES, CODEX, COPILOT, TOKEN_FIELDS, Turn, merge

VERSION = 1
MAX_BYTES = 200 * 1024 * 1024
MAX_RECORDS = 500_000
HISTORY_FIELDS = (
    "day",
    "model",
    "responses",
    "input",
    "cache_5m",
    "cache_1h",
    "cache_read",
    "output",
    "thinking",
    "web_search",
    "cost",
)
CLOUD_FIELDS = (
    "id",
    "origin",
    "surface",
    "project",
    "model",
    "started",
    "ended",
    "input",
    "cache_write",
    "cache_read",
    "output",
    "cost",
    "context_used",
    "context_max",
    "title",
)


def export_file(
    conn: sqlite3.Connection, path: Path, *, include_titles: bool = False
) -> dict:
    """Export only pricing/rollup inputs; never credentials, quota or raw logs."""
    count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    count += conn.execute(
        "SELECT (SELECT COUNT(*) FROM history_days)+(SELECT COUNT(*) FROM imported_history)+(SELECT COUNT(*) FROM sessions WHERE harvested=1)"
    ).fetchone()[0]
    if include_titles:
        count += conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT t.machine_id,t.session FROM turns t JOIN titles ON titles.session=t.session)"
        ).fetchone()[0]
    if count > MAX_RECORDS:
        raise ValueError("Export exceeds 500,000 response records")
    rows = []
    for row in conn.execute(
        f"SELECT {store.TURN_COLUMNS} FROM turns ORDER BY machine_id,msg_id,request_id"
    ):
        turn = store._row_turn(row)
        item = asdict(turn)
        item["ts"] = turn.ts.isoformat()
        rows.append(item)
    account = conn.execute("SELECT value FROM meta WHERE key='account_id'").fetchone()[
        0
    ]
    payload = {
        "format": "vibewatt",
        "version": VERSION,
        "account": account,
        "turns": rows,
    }
    machine = conn.execute(
        "SELECT value FROM meta WHERE key='history_machine_id'"
    ).fetchone()[0]
    payload["history"] = [
        dict(r, machine_id=machine) for r in conn.execute("SELECT * FROM history_days")
    ]
    payload["history"] += [
        dict(r) for r in conn.execute("SELECT * FROM imported_history")
    ]
    payload["cloud"] = [
        dict(r)
        for r in conn.execute(
            "SELECT " + ",".join(CLOUD_FIELDS) + " FROM sessions WHERE harvested=1"
        )
    ]
    if not include_titles:
        for cloud in payload["cloud"]:
            cloud["title"] = None
    if include_titles:
        payload["titles"] = [
            dict(row)
            for row in conn.execute(
                "SELECT DISTINCT t.machine_id, t.session, titles.text FROM turns t JOIN titles ON titles.session=t.session"
            )
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".vibewatt-export-", dir=path.parent)
    size = 0
    try:
        with os.fdopen(fd, "wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
                for chunk in json.JSONEncoder(
                    ensure_ascii=False, allow_nan=False
                ).iterencode(payload):
                    encoded = chunk.encode("utf-8")
                    size += len(encoded)
                    if size > MAX_BYTES:
                        raise ValueError("Export exceeds 200 MB")
                    compressed.write(encoded)
            if output.tell() > MAX_BYTES:
                raise ValueError("Compressed export exceeds 200 MB")
        os.replace(temporary, path)
    finally:
        # Incomplete exports must never replace the user's previous archive.
        Path(temporary).unlink(missing_ok=True)
    return {"records": count, "bytes": size, "account": account}


def _session(machine: str, session: str) -> str:
    prefix = f"vwx:{machine}:"
    raw = session.removeprefix(prefix)
    return raw if machine == identity.machine_id() else prefix + raw


def _validate(payload: object) -> tuple[str, list[Turn], list[dict]]:
    if (
        not isinstance(payload, dict)
        or payload.get("format") != "vibewatt"
        or type(payload.get("version")) is not int
        or payload.get("version") != VERSION
    ):
        raise ValueError("Unsupported vibewatt archive version")
    account = identity.valid_account(payload.get("account", ""))
    rows = payload.get("turns")
    titles = payload.get("titles", [])
    if (
        not isinstance(rows, list)
        or len(rows) > MAX_RECORDS
        or not isinstance(titles, list)
        or len(titles) > MAX_RECORDS
    ):
        raise ValueError("Archive exceeds record limits or has invalid records")
    expected = {field.name for field in fields(Turn)}
    # Archives written before Phase 10 have no billed_usd; it defaults to None.
    optional = {"billed_usd"}
    turns = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not (expected - optional) <= set(row) <= expected
        ):
            raise ValueError("Invalid response fields")
        billed = row.get("billed_usd")
        if billed is not None and (
            isinstance(billed, bool)
            or not isinstance(billed, (int, float))
            or not 0 <= billed <= 1_000_000
        ):
            raise ValueError("Invalid billed amount")
        for name in TOKEN_FIELDS:
            if type(row[name]) is not int or not 0 <= row[name] <= 1_000_000_000_000:
                raise ValueError("Invalid response counter")
        if row["account_id"] != account:
            raise ValueError("Mixed accounts in archive")
        machine = str(UUID(row["machine_id"]))
        if row["source"] not in (*CLAUDE_SOURCES, CODEX, COPILOT):
            raise ValueError("Unknown response source")
        for name in ("model", "project", "session"):
            if not isinstance(row[name], str) or len(row[name]) > 32768:
                raise ValueError("Invalid response metadata")
        key = row["key"]
        if (
            not isinstance(key, list)
            or len(key) != 2
            or any(not isinstance(k, str) or len(k) > 32768 for k in key)
        ):
            raise ValueError("Invalid response identity")
        if type(row["fast"]) is not bool or type(row["sidechain"]) is not bool:
            raise ValueError("Invalid response flags")
        for name in ("geo", "version"):
            if row[name] is not None and (
                not isinstance(row[name], str) or len(row[name]) > 1024
            ):
                raise ValueError("Invalid response metadata")
        stamp = datetime.fromisoformat(row["ts"])
        if stamp.tzinfo is None or not 1970 <= stamp.year <= 9998:
            raise ValueError("Invalid response timestamp")
        turns.append(
            Turn(
                **{
                    **row,
                    "ts": stamp.astimezone(UTC),
                    "key": tuple(key),
                    "machine_id": machine,
                }
            )
        )
    known = {(t.machine_id, t.session) for t in turns}
    for title in titles:
        if not isinstance(title, dict) or set(title) != {
            "machine_id",
            "session",
            "text",
        }:
            raise ValueError("Invalid title fields")
        if (
            not all(isinstance(v, str) for v in title.values())
            or len(title["text"]) > 32768
            or (title["machine_id"], title["session"]) not in known
        ):
            raise ValueError("Invalid title identity")
    return account, turns, titles


def import_file(path: Path, cfg: dict, tz: tzinfo) -> dict:
    """Validate the complete bounded archive before opening any writable store."""
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Archive exceeds 200 MB")
    try:
        with gzip.open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Expanded archive exceeds 200 MB")
        payload = json.loads(raw)
        account, turns, titles = _validate(payload)
        history, cloud = _validate_inputs(payload)
        if len(turns) + len(titles) + len(history) + len(cloud) > MAX_RECORDS:
            raise ValueError("Archive exceeds 500,000 records")
    except (
        OSError,
        EOFError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise ValueError(f"Invalid archive: {exc}") from exc
    grouped: dict[tuple, Turn] = {}
    for turn in turns:
        turn = replace(turn, session=_session(turn.machine_id, turn.session))
        key = (turn.machine_id, *turn.key)
        grouped[key] = merge(grouped[key], turn) if key in grouped else turn
    main_ids = {(t.machine_id, t.key[0]) for t in grouped.values() if not t.sidechain}
    changed = 0
    with identity.scope(account), store.connect() as conn:
        pending = []
        for key, turn in grouped.items():
            if turn.sidechain and (
                (turn.machine_id, turn.key[0]) in main_ids
                or conn.execute(
                    "SELECT 1 FROM turns WHERE machine_id=? AND msg_id=? AND sidechain=0",
                    (turn.machine_id, turn.key[0]),
                ).fetchone()
            ):
                continue
            old = conn.execute(
                f"SELECT {store.TURN_COLUMNS} FROM turns WHERE machine_id=? AND msg_id=? AND request_id=?",
                key,
            ).fetchone()
            if old is None or merge(store._row_turn(old), turn) != store._row_turn(old):
                pending.append(turn)
        hours = store.upsert_turns(
            conn, pending, tz, lambda t: cost_of(t, cfg.get("pricing_overrides"))
        )
        changed = len(pending)
        if hours:
            store.rebuild_rollup(conn, hours)
        for title in titles:
            session = _session(title["machine_id"], title["session"])
            old = conn.execute(
                "SELECT text FROM titles WHERE session=?", (session,)
            ).fetchone()
            if not old:
                conn.execute(
                    "INSERT INTO titles(session,kind,rank,ts,text) VALUES (?,'imported',0,NULL,?)",
                    (session, title["text"]),
                )
                changed += 1
        changed += _import_inputs(conn, history, cloud)
        if changed:
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('generation',?)",
                (str(store.generation(conn) + 1),),
            )
    return {
        "account": account,
        "records": len(turns) + len(history) + len(cloud),
        "changed": changed,
        "bytes": len(raw),
    }


def _validate_inputs(payload: dict) -> tuple[list[dict], list[dict]]:
    history, cloud = payload.get("history", []), payload.get("cloud", [])
    if not isinstance(history, list) or not isinstance(cloud, list):
        raise TypeError("Invalid rollup inputs")
    for row in history:
        if not isinstance(row, dict) or set(row) != {*HISTORY_FIELDS, "machine_id"}:
            raise ValueError("Invalid history fields")
        row["machine_id"] = str(UUID(row["machine_id"]))
        day = date.fromisoformat(row["day"])
        if (
            not 1970 <= day.year <= 9998
            or not isinstance(row["model"], str)
            or len(row["model"]) > 32768
        ):
            raise ValueError("Invalid history identity")
        for key in HISTORY_FIELDS[2:-1]:
            _counter(row[key])
        _money(row["cost"])
    for row in cloud:
        if not isinstance(row, dict) or set(row) != set(CLOUD_FIELDS):
            raise ValueError("Invalid cloud fields")
        for key in ("id", "origin", "surface", "project", "model", "title"):
            if row[key] is not None and (
                not isinstance(row[key], str) or len(row[key]) > 32768
            ):
                raise ValueError("Invalid cloud metadata")
        if not row["id"] or row["surface"] not in ("web", "cowork", "claude-code"):
            raise ValueError("Invalid cloud identity")
        for key in ("started", "ended"):
            if row[key] is not None:
                when = datetime.fromisoformat(row[key])
                if when.tzinfo is None or not 1970 <= when.year <= 9998:
                    raise ValueError("Invalid cloud date")
        for key in (
            "input",
            "cache_write",
            "cache_read",
            "output",
            "context_used",
            "context_max",
        ):
            if row[key] is not None:
                _counter(row[key])
        _money(row["cost"])
    return history, cloud


def _counter(value) -> None:
    if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
        raise ValueError("Invalid aggregate counter")


def _money(value) -> None:
    if value is not None and (
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
    ):
        raise ValueError("Invalid reported cost")


def _maximum(a, b):
    return max(a, b) if a is not None and b is not None else a if b is None else b


def _import_inputs(conn, history: list[dict], cloud: list[dict]) -> int:
    changed = 0
    local = conn.execute(
        "SELECT value FROM meta WHERE key='history_machine_id'"
    ).fetchone()[0]
    for row in history:
        own = row["machine_id"] == local
        table = "history_days" if own else "imported_history"
        keys = ("day", "model") if own else ("machine_id", "day", "model")
        cols = HISTORY_FIELDS if own else ("machine_id", *HISTORY_FIELDS)
        old = conn.execute(
            f"SELECT * FROM {table} WHERE " + " AND ".join(f"{key}=?" for key in keys),
            [row[key] for key in keys],
        ).fetchone()
        merged = {key: row[key] for key in cols}
        if old:
            for key in HISTORY_FIELDS[2:]:
                merged[key] = _maximum(old[key], row[key])
        if old and dict(old) == merged:
            continue
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ("
            + ",".join(cols)
            + ") VALUES ("
            + ",".join("?" for _ in cols)
            + ")",
            [merged[key] for key in cols],
        )
        changed += 1
    local_sessions = store.local_session_ids(conn)
    for row in cloud:
        if row["id"] in local_sessions:
            continue
        old = conn.execute(
            "SELECT " + ",".join(CLOUD_FIELDS) + " FROM sessions WHERE id=?",
            (row["id"],),
        ).fetchone()
        merged = dict(row)
        if old:
            for key in ("input", "cache_write", "cache_read", "output", "cost"):
                merged[key] = _maximum(old[key], row[key])
            if (old["ended"] or "") > (row["ended"] or ""):
                for key in (
                    "origin",
                    "surface",
                    "project",
                    "model",
                    "started",
                    "ended",
                    "context_used",
                    "context_max",
                ):
                    merged[key] = old[key]
            merged["title"] = old["title"] or row["title"]
        if old and dict(old) == merged:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO sessions ("
            + ",".join(CLOUD_FIELDS)
            + ",harvested,raw) VALUES ("
            + ",".join("?" for _ in CLOUD_FIELDS)
            + ",1,NULL)",
            [merged[key] for key in CLOUD_FIELDS],
        )
        changed += 1
    return changed
