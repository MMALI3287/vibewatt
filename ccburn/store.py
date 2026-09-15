"""SQLite store.

Reparsing every JSONL on each render is fine for one machine-month and painful
for a year of history. The store keeps parsed turns keyed by their response id,
so a re-scan is an upsert rather than a recount, and the UI queries SQL instead
of walking files.

It also holds harvested cloud sessions, which have no local file to re-read and
would otherwise be lost the moment the container is reclaimed.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import data_dir

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- One row per billable response. The primary key is what makes a re-scan
-- idempotent: the same response seen again overwrites itself.
CREATE TABLE IF NOT EXISTS turns (
  msg_id      TEXT NOT NULL,
  request_id  TEXT NOT NULL,
  ts          TEXT NOT NULL,       -- ISO 8601 UTC
  day         TEXT NOT NULL,       -- local date, precomputed for grouping
  source      TEXT NOT NULL,       -- claude-code | cowork
  project     TEXT NOT NULL,
  session     TEXT NOT NULL,
  model       TEXT NOT NULL,
  input       INTEGER NOT NULL DEFAULT 0,
  cache_5m    INTEGER NOT NULL DEFAULT 0,
  cache_1h    INTEGER NOT NULL DEFAULT 0,
  cache_read  INTEGER NOT NULL DEFAULT 0,
  output      INTEGER NOT NULL DEFAULT 0,
  thinking    INTEGER NOT NULL DEFAULT 0,
  web_search  INTEGER NOT NULL DEFAULT 0,
  sidechain   INTEGER NOT NULL DEFAULT 0,
  fast        INTEGER NOT NULL DEFAULT 0,
  geo         TEXT,
  cost        REAL,
  PRIMARY KEY (msg_id, request_id)
);
CREATE INDEX IF NOT EXISTS turns_day ON turns(day);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session);
CREATE INDEX IF NOT EXISTS turns_model ON turns(model);

-- Sessions, both local (derived from turns) and cloud (harvested). Cloud rows
-- carry totals only: the API reports a session's usage, not its turns.
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  title       TEXT,
  origin      TEXT,                -- web_claude_ai | claude_code_cli | cowork-* | local
  surface     TEXT,                -- claude-code | cowork | web
  project     TEXT,
  model       TEXT,
  started     TEXT,
  ended       TEXT,
  input       INTEGER DEFAULT 0,
  cache_write INTEGER DEFAULT 0,
  cache_read  INTEGER DEFAULT 0,
  output      INTEGER DEFAULT 0,
  cost        REAL DEFAULT 0,
  context_used   INTEGER,
  context_max    INTEGER,
  harvested   INTEGER NOT NULL DEFAULT 0,   -- 1 = came from the session API
  raw         TEXT
);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started);

-- Prompt titles recovered from last-prompt records, for "what you worked on".
CREATE TABLE IF NOT EXISTS prompts (
  session   TEXT NOT NULL,
  ts        TEXT,
  text      TEXT NOT NULL,
  PRIMARY KEY (session, text)
);

-- Tracks file-level metadata for re-syncs so unchanged JSONL files are skipped.
CREATE TABLE IF NOT EXISTS files (
  path        TEXT PRIMARY KEY,
  mtime       REAL NOT NULL,
  size        INTEGER NOT NULL,
  parsed_at   TEXT NOT NULL,
  turn_count  INTEGER NOT NULL DEFAULT 0
);

-- One sample per quota read, enough to detect burn/spike trends over time.
CREATE TABLE IF NOT EXISTS quota_samples (
  ts          TEXT NOT NULL,
  label       TEXT NOT NULL,
  utilization REAL NOT NULL,
  resets_at   TEXT
);
"""


def db_path() -> Path:
    return data_dir() / "ccburn.db"


def _migrate_schema(conn):
    current = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    current_version = int((current[0] if current else "0")) if current else 0
    if current_version >= SCHEMA_VERSION:
        return
    if current_version < 2:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))


@contextmanager
def connect(path: Path | None = None):
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        _migrate_schema(conn)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_turns(conn, turns, tz, cost_of) -> int:
    """Write parsed turns. Re-running over the same files changes nothing."""
    rows = []
    for t in turns:
        rows.append((
            t.key[0] or f"{t.session}:{t.ts.isoformat()}", t.key[1] or "",
            t.ts.astimezone(timezone.utc).isoformat(),
            t.ts.astimezone(tz).date().isoformat(),
            t.source, t.project, t.session, t.model,
            t.input, t.cache_5m, t.cache_1h, t.cache_read, t.output,
            t.thinking, t.web_searches, int(t.sidechain), int(t.fast), t.geo,
            cost_of(t),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def upsert_quota_samples(conn, quota) -> int:
    """Persist quota samples so a single reading is not mistaken for a trend."""
    if quota is None or not getattr(quota, "windows", None):
        return 0
    rows = []
    ts = quota.fetched_at.isoformat() if getattr(quota, "fetched_at", None) else datetime.now(timezone.utc).isoformat()
    for window in quota.windows:
        rows.append((
            ts,
            window.label,
            float(window.utilization),
            window.resets_at.isoformat() if window.resets_at else None,
        ))
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO quota_samples (ts, label, utilization, resets_at) VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def sync_directory(conn, files, tz, cost_of=None) -> int:
    """Read only changed JSONL files and record file metadata for future skips."""
    from .sources import load

    if cost_of is None:
        from .aggregate import cost_of as _cost_of
        cost_of = _cost_of

    new_turns = 0
    seen = {row[0]: row for row in conn.execute("SELECT path, mtime, size, turn_count FROM files").fetchall()}
    for source, path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        key = str(path)
        mtime = stat.st_mtime
        size = stat.st_size
        if key in seen and float(seen[key]["mtime"]) == mtime and int(seen[key]["size"]) == size:
            continue
        turns, _ = load([(source, path)])
        if turns:
            new_turns += upsert_turns(conn, turns, tz, cost_of)
        conn.execute(
            "INSERT INTO files (path, mtime, size, parsed_at, turn_count) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size, "
            "parsed_at = excluded.parsed_at, turn_count = excluded.turn_count",
            (key, mtime, size, datetime.now(timezone.utc).isoformat(), len(turns)),
        )
    return new_turns


def upsert_prompts(conn, prompts) -> int:
    rows = [(p["session"], p.get("ts"), p["text"]) for p in prompts if p.get("text")]
    conn.executemany("INSERT OR REPLACE INTO prompts VALUES (?,?,?)", rows)
    return len(rows)


_SURFACE = {
    "web_claude_ai": "web",
    "claude_code_cli": "claude-code",
    "claude_code_vscode": "claude-code",
}


def _repo_name(ctx: dict) -> str | None:
    for src in (ctx or {}).get("sources") or []:
        url = (src.get("git_repository") or {}).get("url")
        if url:
            return url.rstrip("/").rsplit("/", 1)[-1]
    return None


def upsert_cloud_sessions(conn, payload) -> tuple[int, int]:
    """Ingest a session listing from the Claude Code session API.

    Returns (written, skipped). Sessions with no usage block are skipped rather
    than stored as zeroes, so an unstarted session cannot look like free work.
    """
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return 0, 0
    written = skipped = 0
    rows = []
    for s in entries:
        if not isinstance(s, dict):
            continue
        meta = s.get("external_metadata") or {}
        usage = meta.get("usage") or {}
        if not usage:
            skipped += 1
            continue
        ctx = s.get("session_context") or {}
        tags = s.get("tags") or []
        origin = s.get("origin") or ""
        surface = _SURFACE.get(origin, "claude-code")
        for tag in tags:
            if str(tag).startswith("cowork"):
                surface = "cowork"
        cu = meta.get("context_usage") or {}
        rows.append((
            s.get("id"), s.get("title"), origin, surface,
            _repo_name(ctx), (ctx.get("model") or meta.get("model")),
            s.get("created_at"), s.get("updated_at"),
            int(usage.get("input_tokens") or 0),
            int(usage.get("cache_write_tokens") or 0),
            int(usage.get("cache_read_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            float(usage.get("cost_usd") or 0.0),
            cu.get("used_tokens"), cu.get("max_tokens"),
            1, json.dumps(s, separators=(",", ":"))[:20000],
        ))
        written += 1
    conn.executemany(
        "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_harvest', ?)",
                 (datetime.now(timezone.utc).isoformat(),))
    return written, skipped


def summary(conn) -> dict:
    def one(sql, *args):
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row else {}

    turns = one("SELECT COUNT(*) n, MIN(day) lo, MAX(day) hi, SUM(cost) cost FROM turns")
    cloud = one("SELECT COUNT(*) n, SUM(cost) cost FROM sessions WHERE harvested=1")
    by_surface = [dict(r) for r in conn.execute(
        "SELECT surface, COUNT(*) n, SUM(cost) cost FROM sessions WHERE harvested=1"
        " GROUP BY surface ORDER BY cost DESC")]
    prompts = one("SELECT COUNT(*) n FROM prompts")
    last = one("SELECT value FROM meta WHERE key='last_harvest'")
    return {"turns": turns, "cloud": cloud, "cloud_by_surface": by_surface,
            "prompts": prompts, "last_harvest": last.get("value")}
