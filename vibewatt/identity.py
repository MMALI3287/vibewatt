"""Local identity and explicit account isolation for every store consumer."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID, uuid4

from .config import data_dir, user_config_dir

_selected: ContextVar[str | None] = ContextVar("vibewatt_account", default=None)
_machine_lock = threading.Lock()


def valid_account(value: str) -> str:
    return value if value == "unknown" else str(UUID(value))


def account_id() -> str:
    """Read only the OAuth account UUID, never a token or email address."""
    from .ingest.claude_code import roots

    candidates = [root.parent / ".claude.json" for root in roots()]
    if not os.environ.get("CLAUDE_CONFIG_DIR"):
        candidates.append(Path.home() / ".claude.json")
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return valid_account(raw["oauthAccount"]["accountUuid"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return "unknown"


def selected_account() -> str:
    return _selected.get() or account_id()


@contextmanager
def scope(account: str):
    token = _selected.set(valid_account(account))
    try:
        yield
    finally:
        _selected.reset(token)


def machine_id() -> str:
    path = user_config_dir() / "machine-id"
    with _machine_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="ascii") as stream:
                stream.write(str(uuid4()))
        except FileExistsError:
            pass
        return str(UUID(path.read_text(encoding="ascii").strip()))


def owner(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='account_id'"
            ).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def database_path() -> Path:
    base = data_dir() / "vibewatt.db"
    account = selected_account()
    # The original file stays in place. Switching identity never reattributes it.
    original = owner(base) or account_id()
    return (
        base
        if account == original
        else data_dir() / "accounts" / f"{valid_account(account)}.db"
    )


def accounts() -> list[str]:
    base = data_dir()
    found = {account_id(), selected_account()}
    for path in [base / "vibewatt.db", *sorted((base / "accounts").glob("*.db"))]:
        account = owner(path)
        if account:
            found.add(account)
    return sorted(found)


def foreign_records(table: str, columns: str) -> set[tuple]:
    """Already attributed local evidence must not migrate with a login change."""
    records = set()
    base = data_dir()
    for path in [base / "vibewatt.db", *sorted((base / "accounts").glob("*.db"))]:
        if not path.is_file() or (owner(path) or account_id()) == selected_account():
            continue
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                records.update(conn.execute(f"SELECT {columns} FROM {table}"))
    return records


def unclaimed_files(files: list) -> list:
    claimed = {str(Path(row[0]).resolve()) for row in foreign_records("files", "path")}
    return [
        (source, path) for source, path in files if str(path.resolve()) not in claimed
    ]
