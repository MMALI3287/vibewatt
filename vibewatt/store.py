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
import os
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

from .config import clock_zone, copy_sqlite, data_dir, zone_id
from .sources import CLAUDE_SOURCES, source_clause

SCHEMA_VERSION = 13

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

-- One title per session for "what you worked on": the best kind seen
-- (custom-title > ai-title > last-prompt > first user message). Only the
-- title is kept, never the prompts before it.
CREATE TABLE IF NOT EXISTS titles (
  session   TEXT PRIMARY KEY,
  kind      TEXT NOT NULL,
  rank      INTEGER NOT NULL,
  ts        TEXT,
  text      TEXT NOT NULL
);

"""


def db_path() -> Path:
    from .identity import database_path

    path = database_path()
    legacy = path.with_name("ccburn.db")
    if not path.exists() and legacy.is_file():
        # A CCBURN_DATA_DIR store; the old file stays for a downgrade.
        copy_sqlite(legacy, path)
    return path


def _v2_files_and_quota_samples(conn) -> None:
    conn.executescript("""
    -- (mtime, size) per file, so a re-sync skips files that have not changed.
    CREATE TABLE IF NOT EXISTS files (
      path        TEXT PRIMARY KEY,
      mtime       REAL NOT NULL,
      size        INTEGER NOT NULL,
      parsed_at   TEXT NOT NULL,
      turn_count  INTEGER NOT NULL DEFAULT 0
    );

    -- A series of utilization readings. Keyed so re-reading the same cached
    -- statusline dump does not fake a trend out of one sample.
    CREATE TABLE IF NOT EXISTS quota_samples (
      ts          TEXT NOT NULL,
      label       TEXT NOT NULL,
      utilization REAL NOT NULL,
      resets_at   TEXT,
      PRIMARY KEY (ts, label)
    );
    """)


def _v3_key_quota_samples_and_resync(conn) -> None:
    # The first Phase 1 attempt stamped schema 2 with an unkeyed quota_samples,
    # so re-reading one statusline dump stored it again and faked a trend. Its
    # files rows also skipped prompt capture, so forget them and re-read once.
    # One transaction, and a leftover quota_samples_unkeyed from a crashed run is
    # finished rather than stranded.
    has_key = any(col["pk"] for col in conn.execute("PRAGMA table_info(quota_samples)"))
    stranded = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'quota_samples_unkeyed'"
    ).fetchone()
    steps = ["BEGIN;"]
    if not has_key:
        steps.append("""
        ALTER TABLE quota_samples RENAME TO quota_samples_unkeyed;
        CREATE TABLE quota_samples (
          ts          TEXT NOT NULL,
          label       TEXT NOT NULL,
          utilization REAL NOT NULL,
          resets_at   TEXT,
          PRIMARY KEY (ts, label)
        );""")
    if not has_key or stranded:
        steps.append("""
        INSERT OR IGNORE INTO quota_samples SELECT ts, label, utilization, resets_at
          FROM quota_samples_unkeyed;
        DROP TABLE quota_samples_unkeyed;""")
    steps.append("DELETE FROM files; COMMIT;")
    conn.executescript("\n".join(steps))


def _v4_analysis(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS findings (
      id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
      severity TEXT NOT NULL, day TEXT, subject TEXT NOT NULL,
      detail_json TEXT NOT NULL, created_at TEXT NOT NULL,
      dismissed INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS findings_scope ON findings(scope, active);
    CREATE TABLE IF NOT EXISTS tool_reads (
      session TEXT NOT NULL, tool_id TEXT NOT NULL, ts TEXT NOT NULL,
      source TEXT NOT NULL, project TEXT NOT NULL, model TEXT NOT NULL,
      path_hash TEXT NOT NULL, PRIMARY KEY(session, tool_id)
    );
    CREATE TABLE IF NOT EXISTS tool_read_files (
      path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL
    );
    """)


ROLLUP_COLUMNS = """
      hr TEXT NOT NULL, day TEXT NOT NULL, hour INTEGER,
      source TEXT NOT NULL, project TEXT NOT NULL, model TEXT NOT NULL,
      session TEXT NOT NULL, sidechain INTEGER NOT NULL,
      responses INTEGER NOT NULL, input INTEGER NOT NULL, cache_5m INTEGER NOT NULL,
      cache_1h INTEGER NOT NULL, cache_read INTEGER NOT NULL, output INTEGER NOT NULL,
      thinking INTEGER NOT NULL, web_search INTEGER NOT NULL,
      cost REAL, unpriced INTEGER NOT NULL, first_ts TEXT NOT NULL, last_ts TEXT NOT NULL
"""


def _v5_dedup_and_rollup(conn) -> None:
    # Phase 6.5b: the per-field-maximum dedup rule, the store as the only source
    # of report numbers plus the one-time import of the retired history.json.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
    if "version" not in cols:
        conn.execute("ALTER TABLE turns ADD COLUMN version TEXT")
    if "hour" not in cols:
        conn.execute("ALTER TABLE turns ADD COLUMN hour INTEGER")
    conn.executescript(f"""
    CREATE INDEX IF NOT EXISTS turns_hr ON turns(substr(ts, 1, 13));

    -- Every report endpoint reads this, never the logs. One row per UTC hour,
    -- local day and hour plus (source, project, model, session, sidechain).
    -- Blocks can be rebuilt exactly from it: a block ends on an hour boundary
    -- and a 5 h gap cannot fall inside one hour.
    CREATE TABLE IF NOT EXISTS rollup ({ROLLUP_COLUMNS});
    CREATE INDEX IF NOT EXISTS rollup_day ON rollup(day);
    CREATE INDEX IF NOT EXISTS rollup_hr ON rollup(hr);

    -- Raw rows of the retired history.json. Reports add only the part by which
    -- a day exceeds what the store holds, so an import can never double count.
    CREATE TABLE IF NOT EXISTS history_days (
      day TEXT NOT NULL, model TEXT NOT NULL,
      responses INTEGER NOT NULL DEFAULT 0, input INTEGER NOT NULL DEFAULT 0,
      cache_5m INTEGER NOT NULL DEFAULT 0, cache_1h INTEGER NOT NULL DEFAULT 0,
      cache_read INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,
      thinking INTEGER NOT NULL DEFAULT 0, web_search INTEGER NOT NULL DEFAULT 0,
      cost REAL,
      PRIMARY KEY (day, model)
    );

    -- Re-read every file under the new dedup rule. A changed sync_tz forces the
    -- rebucket that fills turns.hour and a full rollup rebuild.
    DELETE FROM files;
    INSERT OR REPLACE INTO meta VALUES ('sync_tz', 'schema-5');
    """)


def _v6_titles_and_drops(conn) -> None:
    # Phase 6.5c. prompts kept every distinct last-prompt text of a session
    # (A-067); titles keeps one. The latest stored prompt becomes the title and
    # the prompts table is dropped. Files are re-read once so custom and AI
    # titles and per-file drop counts are picked up.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    if "dropped" not in cols:
        conn.execute("ALTER TABLE files ADD COLUMN dropped TEXT")
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'prompts'"
    ).fetchone():
        conn.execute(
            "INSERT OR REPLACE INTO titles (session, kind, rank, ts, text)"
            " SELECT session, 'last-prompt', 2, ts, text FROM prompts"
            " ORDER BY ts IS NOT NULL, ts, text"
        )
        conn.execute("DROP TABLE prompts")
    conn.execute("DELETE FROM files")


_LEGACY_QUOTA_KEYS = {
    "5-hour": "five_hour",
    "7-day": "seven_day",
    "5-hour (Opus)": "five_hour_opus",
    "7-day (Opus)": "seven_day_opus",
}


def _v7_quota_windows(conn) -> None:
    # Phase 6.5d: a sample is one window from one source, keyed on the window
    # (five_hour, seven_day, ...) and its scope (the account, or a desktop org)
    # instead of a display label, so several sources feed one series.
    conn.execute("ALTER TABLE quota_samples RENAME TO quota_samples_v2")
    conn.execute("""
    CREATE TABLE quota_samples (
      ts          TEXT NOT NULL,
      key         TEXT NOT NULL,
      label       TEXT NOT NULL,
      scope       TEXT NOT NULL,
      utilization REAL NOT NULL,
      resets_at   TEXT,
      source      TEXT NOT NULL,
      PRIMARY KEY (ts, key, scope)
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS quota_samples_series"
        " ON quota_samples(key, scope, ts)"
    )
    rows = [
        (
            r[0],
            _LEGACY_QUOTA_KEYS.get(r[1], r[1].lower().replace(" ", "_")),
            r[1],
            "account",
            r[2],
            r[3],
            "legacy",
        )
        for r in conn.execute(
            "SELECT ts, label, utilization, resets_at FROM quota_samples_v2"
        )
    ]
    conn.executemany("INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?,?,?,?)", rows)
    conn.execute("DROP TABLE quota_samples_v2")


def _v8_keyed_path_hash(conn) -> None:
    # Phase 6.5e: path hashes become an HMAC under a per-install key, so a
    # guessed path cannot be confirmed from a copied store (A-121). Old
    # unkeyed hashes cannot be converted without the paths, so tool reads are
    # read again from the logs that still exist.
    conn.execute("DELETE FROM tool_reads")
    conn.execute("DELETE FROM tool_read_files")


def _v9_dismissals(conn) -> None:
    # Phase 6.5f: dismissals outlive a snapshot and apply across filters for
    # session findings (A-091). Existing dismissals are carried over.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dismissals (key TEXT PRIMARY KEY, at TEXT)"
    )
    # Line counts as Claude's own Stats count them, for the reconciliation
    # panel. Filled by re-reading every file once.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS raw_lines (
      path TEXT NOT NULL, source TEXT NOT NULL, hr TEXT NOT NULL, session TEXT NOT NULL,
      messages INTEGER NOT NULL, tokens INTEGER NOT NULL,
      PRIMARY KEY (path, hr, session)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS raw_lines_hr ON raw_lines(hr)")
    conn.execute("DELETE FROM files")
    for row in conn.execute(
        "SELECT scope, detail_json, created_at FROM findings WHERE dismissed = 1"
    ).fetchall():
        item = json.loads(row[1])
        if "rule" in item:
            conn.execute(
                "INSERT OR IGNORE INTO dismissals VALUES (?, ?)",
                (dismissal_key(item, row[0]), row[2]),
            )
    # Snapshots left inactive by earlier versions are not history anyone reads.
    conn.execute(
        "DELETE FROM findings WHERE active = 0 AND dismissed = 0"
        " AND scope != 'phase6-alerts'"
    )


def _v10_tool_usage(conn) -> None:
    for table in ("turns", "rollup"):
        for column in (
            "web_fetch",
            "code_execution",
            "nonstandard_iterations",
            "context_premium_unknown",
        ):
            if column not in {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                )
    # Re-read retained transcripts once to populate newly available counters.
    conn.execute("UPDATE files SET mtime = -1")


def _v11_activity(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS activity (ts TEXT NOT NULL, project TEXT NOT NULL, PRIMARY KEY(ts, project))"
    )


def _v12_identity(conn) -> None:
    from . import identity

    account, machine = identity.selected_account(), identity.machine_id()
    columns = list(conn.execute("PRAGMA table_info(turns)"))
    if "account_id" in {column[1] for column in columns}:
        return
    definitions = []
    for column in columns:
        definition = f'"{column[1]}" {column[2]}'
        if column[3]:
            definition += " NOT NULL"
        if column[4] is not None:
            definition += f" DEFAULT {column[4]}"
        definitions.append(definition)
    definitions += [
        f"account_id TEXT NOT NULL DEFAULT '{account}'",
        f"machine_id TEXT NOT NULL DEFAULT '{machine}'",
        "PRIMARY KEY(account_id, machine_id, msg_id, request_id)",
    ]
    conn.execute("CREATE TABLE turns_identity (" + ",".join(definitions) + ")")
    names = ",".join(f'"{column[1]}"' for column in columns)
    conn.execute(
        f"INSERT INTO turns_identity ({names},account_id,machine_id) SELECT {names},?,? FROM turns",
        (account, machine),
    )
    # Replace only the table definition to expand response identity; all rows survive.
    conn.execute("DROP TABLE turns")
    conn.execute("ALTER TABLE turns_identity RENAME TO turns")
    for column in ("day", "session", "model"):
        conn.execute(f"CREATE INDEX turns_{column} ON turns({column})")
    conn.execute("CREATE INDEX turns_hr ON turns(substr(ts, 1, 13))")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('account_id', ?)", (account,))
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('history_machine_id', ?)", (machine,)
    )
    conn.execute(
        f"ALTER TABLE rollup ADD COLUMN machine_id TEXT NOT NULL DEFAULT '{machine}'"
    )
    conn.execute(
        "CREATE TABLE imported_history (machine_id TEXT NOT NULL, day TEXT NOT NULL, model TEXT NOT NULL, responses INTEGER NOT NULL DEFAULT 0, input INTEGER NOT NULL DEFAULT 0, cache_5m INTEGER NOT NULL DEFAULT 0, cache_1h INTEGER NOT NULL DEFAULT 0, cache_read INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0, thinking INTEGER NOT NULL DEFAULT 0, web_search INTEGER NOT NULL DEFAULT 0, cost REAL, PRIMARY KEY(machine_id,day,model))"
    )
    if account != "unknown":
        conn.execute(
            "CREATE TABLE IF NOT EXISTS unattributed_quota_samples AS SELECT * FROM quota_samples"
        )
        conn.execute("DELETE FROM quota_samples")


def _v13_billed_cost(conn) -> None:
    # Phase 10: Copilot logs what it billed per request. It is kept apart from
    # the estimated cost so a repricing never replaces it. NULL for every
    # existing row, so no usage changes.
    columns = {column[1] for column in conn.execute("PRAGMA table_info(turns)")}
    if "billed_usd" not in columns:
        conn.execute("ALTER TABLE turns ADD COLUMN billed_usd REAL")


# Forward-only. Append a step and bump SCHEMA_VERSION; never drop a user's table.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _v2_files_and_quota_samples,
    3: _v3_key_quota_samples_and_resync,
    4: _v4_analysis,
    5: _v5_dedup_and_rollup,
    6: _v6_titles_and_drops,
    7: _v7_quota_windows,
    8: _v8_keyed_path_hash,
    9: _v9_dismissals,
    10: _v10_tool_usage,
    11: _v11_activity,
    12: _v12_identity,
    13: _v13_billed_cost,
}
assert max(MIGRATIONS) == SCHEMA_VERSION


def schema_version(conn) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    return int(row[0]) if row else 1


def migrate(conn) -> None:
    current = schema_version(conn)
    for version in sorted(v for v in MIGRATIONS if v > current):
        MIGRATIONS[version](conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(version),)
        )


# Schema work takes the write lock, so it only runs when meta says the store is
# behind, not on every request (A-056).
_migrate_lock = threading.Lock()
BUSY_TIMEOUT_MS = 30_000


def _current(conn) -> int:
    try:
        return schema_version(conn)
    except sqlite3.OperationalError:
        return 0  # a new file: no meta table yet


@contextmanager
def connect(path: Path | None = None):
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    try:
        # A writer waits up to 30 s for another writer instead of failing at 5 s;
        # WAL readers never wait. Sync commits per batch, so no wait is long.
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if _current(conn) < SCHEMA_VERSION:
            with _migrate_lock:
                if _current(conn) > 0:
                    backup = target.with_name(
                        target.name
                        + ".pre-v"
                        + str(SCHEMA_VERSION)
                        + "-"
                        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
                        + ".bak"
                    )
                    with sqlite3.connect(backup) as destination:
                        conn.backup(destination)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                migrate(conn)
                conn.commit()
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


TURN_COLUMNS = (
    "msg_id, request_id, ts, day, source, project, session, model, input, cache_5m,"
    " cache_1h, cache_read, output, thinking, web_search, sidechain, fast, geo, cost,"
    " version, hour, web_fetch, code_execution, nonstandard_iterations, context_premium_unknown, account_id, machine_id, billed_usd"
)


def _row_turn(row):
    from .sources import Turn

    return Turn(
        source=row["source"],
        ts=datetime.fromisoformat(row["ts"]),
        model=row["model"],
        input=row["input"],
        cache_5m=row["cache_5m"],
        cache_1h=row["cache_1h"],
        cache_read=row["cache_read"],
        output=row["output"],
        thinking=row["thinking"],
        web_searches=row["web_search"],
        fast=bool(row["fast"]),
        geo=row["geo"],
        sidechain=bool(row["sidechain"]),
        project=row["project"],
        session=row["session"],
        key=(row["msg_id"], row["request_id"]),
        version=row["version"],
        web_fetch=row["web_fetch"],
        code_execution=row["code_execution"],
        nonstandard_iterations=row["nonstandard_iterations"],
        account_id=row["account_id"],
        machine_id=row["machine_id"],
        billed_usd=row["billed_usd"],
    )


def _hr(ts: datetime | str) -> str:
    text = ts if isinstance(ts, str) else ts.astimezone(UTC).isoformat()
    return text[:13]


def upsert_turns(conn, turns, tz, cost_of) -> set[str]:
    from dataclasses import replace

    from . import identity
    from .sources import dedupe

    account = conn.execute("SELECT value FROM meta WHERE key='account_id'").fetchone()[
        0
    ]
    grouped = defaultdict(list)
    for turn in turns:
        turn = replace(
            turn,
            account_id=turn.account_id or account,
            machine_id=turn.machine_id or identity.machine_id(),
        )
        if turn.account_id != account:
            raise ValueError("A turn cannot enter another account's store")
        grouped[turn.machine_id].append(turn)
    hours = set()
    for batch in grouped.values():
        deduped, _ = dedupe(batch)
        hours.update(_upsert_machine(conn, deduped, tz, cost_of))
    return hours


def _upsert_machine(conn, turns, tz, cost_of) -> set[str]:
    """Merge deduped turns into the store. Returns the UTC hours touched.

    Merging is order independent: a response already stored takes the
    per-field maximum with the incoming line, a main-thread row evicts a
    sidechain replay of the same message. A sidechain replay of a stored
    main-thread message is ignored. A pre-6.5b row keyed without its request id
    is absorbed by the keyed row that replaces it.
    """
    from .pricing import context_premium_unknown
    from .sources import merge

    turns = list(turns)
    if not turns:
        return set()
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS incoming (msg_id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM incoming")
    conn.executemany(
        "INSERT OR IGNORE INTO incoming VALUES (?)", [(t.key[0],) for t in turns]
    )
    stored: dict[str, list] = defaultdict(list)
    for row in conn.execute(
        f"SELECT {TURN_COLUMNS} FROM turns WHERE machine_id = ? AND msg_id IN (SELECT msg_id FROM incoming)",
        (turns[0].machine_id,),
    ):
        stored[row["msg_id"]].append(row)

    hours: set[str] = set()
    deletes: set[tuple[str, str]] = set()
    writes = []
    for turn in turns:
        merged, skip = turn, False
        for row in stored[turn.key[0]]:
            key = (row["msg_id"], row["request_id"])
            if key == turn.key or (row["request_id"] == "" and turn.key[1]):
                merged = merge(merged, _row_turn(row))
                if key != turn.key:
                    deletes.add(key)
                hours.add(_hr(row["ts"]))
            elif turn.sidechain and not row["sidechain"]:
                skip = True
            elif row["sidechain"] and not turn.sidechain:
                deletes.add(key)
                hours.add(_hr(row["ts"]))
        if skip:
            continue
        local = merged.ts.astimezone(tz)
        clock_hour = merged.ts.astimezone(clock_zone(tz)).hour
        hours.add(_hr(merged.ts))
        writes.append(
            (
                merged.key[0],
                merged.key[1],
                merged.ts.astimezone(UTC).isoformat(),
                local.date().isoformat(),
                merged.source,
                merged.project,
                merged.session,
                merged.model,
                merged.input,
                merged.cache_5m,
                merged.cache_1h,
                merged.cache_read,
                merged.output,
                merged.thinking,
                merged.web_searches,
                int(merged.sidechain),
                int(merged.fast),
                merged.geo,
                cost_of(merged),
                merged.version,
                clock_hour,
                merged.web_fetch,
                merged.code_execution,
                merged.nonstandard_iterations,
                int(context_premium_unknown(merged)),
                merged.account_id,
                merged.machine_id,
                merged.billed_usd,
            )
        )
    conn.executemany(
        "DELETE FROM turns WHERE msg_id = ? AND request_id = ? AND machine_id = ?",
        [(*key, turns[0].machine_id) for key in sorted(deletes)],
    )
    conn.executemany(
        f"INSERT OR REPLACE INTO turns ({TURN_COLUMNS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        writes,
    )
    return hours


_ROLLUP_SELECT = """
    SELECT substr(ts, 1, 13), day, hour, source, project, model, session, sidechain,
      COUNT(*), SUM(input), SUM(cache_5m), SUM(cache_1h), SUM(cache_read), SUM(output),
      SUM(thinking), SUM(web_search), SUM(cost), SUM(cost IS NULL), MIN(ts), MAX(ts),
      SUM(web_fetch), SUM(code_execution), SUM(nonstandard_iterations), SUM(context_premium_unknown), machine_id
    FROM turns {where}
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, machine_id
"""


def rebuild_rollup(conn, hours: Iterable[str] | None = None) -> None:
    """Recompute rollup rows for the given UTC hours, or all of them."""
    if hours is None:
        conn.execute("DELETE FROM rollup")
        conn.execute("INSERT INTO rollup " + _ROLLUP_SELECT.format(where=""))
        return
    ordered = sorted(set(hours))
    for i in range(0, len(ordered), 500):
        chunk = ordered[i : i + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM rollup WHERE hr IN ({marks})", chunk)
        conn.execute(
            "INSERT INTO rollup "
            + _ROLLUP_SELECT.format(where=f"WHERE substr(ts, 1, 13) IN ({marks})"),
            chunk,
        )


def reprice(conn: sqlite3.Connection, overrides: dict | None = None) -> int:
    """Atomically refresh retained local costs when pricing inputs change.

    Call after syncing files using the same overrides. Cloud session totals
    are reported by their source and must never be replaced by estimates.
    The existing meta table tracks the complete store's pricing snapshot so
    this also works after the original logs have disappeared.
    """
    from .aggregate import cost_of
    from .pricing import context_premium_unknown, fingerprint

    current = fingerprint(overrides)
    conn.execute("SAVEPOINT reprice")
    try:
        previous = conn.execute(
            "SELECT value FROM meta WHERE key = 'pricing_fingerprint'"
        ).fetchone()
        changed = 0
        if previous is None or previous[0] != current:
            hours: set[str] = set()
            for row in conn.execute(f"SELECT {TURN_COLUMNS} FROM turns"):
                cost = cost_of(_row_turn(row), overrides)
                unknown = int(context_premium_unknown(_row_turn(row)))
                if cost != row["cost"] or unknown != row["context_premium_unknown"]:
                    conn.execute(
                        "UPDATE turns SET cost = ?, context_premium_unknown = ? WHERE msg_id = ? AND request_id = ? AND machine_id = ?",
                        (
                            cost,
                            unknown,
                            row["msg_id"],
                            row["request_id"],
                            row["machine_id"],
                        ),
                    )
                    changed += 1
                    hours.add(_hr(row["ts"]))
            if hours:
                rebuild_rollup(conn, hours)
            # Savings findings depend on base rates even when a cache-only
            # turn's charged amount is unchanged.
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('generation', ?)",
                (str(generation(conn) + 1),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('pricing_fingerprint', ?)",
                (current,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('last_reprice', ?)",
                (datetime.now(UTC).isoformat(),),
            )
        conn.execute("RELEASE SAVEPOINT reprice")
        return changed
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT reprice")
        conn.execute("RELEASE SAVEPOINT reprice")
        raise


def _path_key(conn) -> bytes:
    """The per-install HMAC key for tool read paths, created on first use."""
    import secrets

    row = conn.execute("SELECT value FROM meta WHERE key = 'path_hash_key'").fetchone()
    if row:
        return bytes.fromhex(row[0])
    key = secrets.token_bytes(32)
    conn.execute("INSERT OR IGNORE INTO meta VALUES ('path_hash_key', ?)", (key.hex(),))
    return bytes.fromhex(
        conn.execute("SELECT value FROM meta WHERE key = 'path_hash_key'").fetchone()[0]
    )


def generation(conn) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'generation'").fetchone()
    return int(row[0]) if row else 0


def import_history(conn) -> int:
    """Copy the retired history.json into history_days, once. Returns rows read."""
    from . import identity

    original = identity.owner(data_dir() / "vibewatt.db")
    if original and original != identity.selected_account():
        return 0
    if conn.execute("SELECT 1 FROM meta WHERE key = 'history_imported'").fetchone():
        return 0
    rows = []
    try:
        with (data_dir() / "history.json").open("r", encoding="utf-8") as fh:
            blob = json.load(fh)
        days = blob.get("days") if isinstance(blob, dict) else None
    except (OSError, json.JSONDecodeError):
        days = None
    for key, row in (days if isinstance(days, dict) else {}).items():
        try:
            day, model = key.split("|", 1)
            date.fromisoformat(day)
            rows.append(
                (
                    day,
                    model,
                    int(row.get("responses", 0)),
                    int(row.get("input", 0)),
                    int(row.get("cache_5m", 0)),
                    int(row.get("cache_1h", 0)),
                    int(row.get("cache_read", 0)),
                    int(row.get("output", 0)),
                    int(row.get("thinking", 0)),
                    int(row.get("web_searches", 0)),
                    float(row.get("cost", 0.0)),
                )
            )
        except (AttributeError, TypeError, ValueError):
            continue
    conn.executemany(
        "INSERT OR REPLACE INTO history_days VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('history_imported', ?)",
        (datetime.now(UTC).isoformat(),),
    )
    return len(rows)


@dataclass
class SyncResult:
    parsed: int = 0
    skipped: int = 0
    turns: int = 0
    duplicates: int = 0
    prompts: int = 0  # session titles written; the API field keeps its name
    unreadable: int = 0  # files that could not be opened; retried next sync


def rebucket(conn, tz) -> bool:
    """Rebucket retained UTC responses without opening any source files."""
    tz_id = zone_id(tz)
    row = conn.execute("SELECT value FROM meta WHERE key='sync_tz'").fetchone()
    if row and row[0] == tz_id:
        return False
    updates = []
    for r in conn.execute("SELECT msg_id,request_id,ts,machine_id FROM turns"):
        stamp = datetime.fromisoformat(r["ts"])
        updates.append(
            (
                stamp.astimezone(tz).date().isoformat(),
                stamp.astimezone(clock_zone(tz)).hour,
                r["msg_id"],
                r["request_id"],
                r["machine_id"],
            )
        )
    conn.executemany(
        "UPDATE turns SET day=?,hour=? WHERE msg_id=? AND request_id=? AND machine_id=?",
        updates,
    )
    rebuild_rollup(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('sync_tz',?)", (tz_id,))
    if updates:
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('generation',?)",
            (str(generation(conn) + 1),),
        )
    return True


class _Stat(NamedTuple):
    st_mtime: float
    st_size: int


def _marker(source: str, path: Path) -> _Stat:
    # A SQLite source can change only in its -wal file, which leaves the
    # database's own mtime and size unchanged until a checkpoint.
    from .sources import ANTIGRAVITY

    if source == ANTIGRAVITY:
        from .ingest.antigravity import change_marker

        return _Stat(*change_marker(path))
    st = path.stat()
    return _Stat(st.st_mtime, st.st_size)


def sync_files(
    conn,
    files: list[tuple[str, Path]],
    tz,
    cost_of,
    *,
    progress: Callable[[int, int], None] | None = None,
    batch_files: int = 200,
) -> SyncResult:
    """Ingest only files whose (mtime, size) changed since the last sync.

    Turns from a file that later disappears stay in the store on purpose: that
    is how history survives Claude Code pruning old transcripts. Files are
    read in batches so a first sync of a large tree keeps memory bounded, and
    `progress(done, total)` is called after each batch.
    """
    from collections import Counter

    from .ingest import parse
    from .sources import dedupe, read_titles

    result = SyncResult()
    full_rebuild = rebucket(conn, tz)
    known = {
        r["path"]: (r["mtime"], r["size"])
        for r in conn.execute("SELECT path, mtime, size FROM files")
    }
    # Separate checkpoints backfill metadata once without deleting usage history
    # or invalidating the existing incremental usage cache.
    from .ingest.tool_reads import read_tools

    reads_known = {
        r["path"]: (r["mtime"], r["size"])
        for r in conn.execute("SELECT * FROM tool_read_files")
    }
    changed: list[tuple[str, Path, os.stat_result]] = []
    for source, path in files:
        try:
            st = _marker(source, path)
        except OSError:
            continue
        # Read-tool metadata and titles exist only in Claude transcripts. Other
        # sources are skipped here so a large binary file is never read as text.
        if source in CLAUDE_SOURCES and reads_known.get(str(path)) != (
            st.st_mtime,
            st.st_size,
        ):
            try:
                reads = list(read_tools(source, path, key=_path_key(conn)))
            except OSError:
                pass
            else:
                conn.executemany(
                    "INSERT OR IGNORE INTO tool_reads VALUES "
                    "(:session, :tool_id, :ts, :source, :project, :model, :path_hash)",
                    reads,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO tool_read_files VALUES (?,?,?)",
                    (str(path), st.st_mtime, st.st_size),
                )
        if known.get(str(path)) == (st.st_mtime, st.st_size):
            result.skipped += 1
        else:
            changed.append((source, path, st))

    # Release the write lock before the slow part: each batch commits on its
    # own, so a request that writes (alerts, dismissals) never waits long.
    conn.commit()
    hours: set[str] = set()
    from . import identity
    from .ingest.codex import plan_readings as codex_plan_readings
    from .sources import CODEX

    codex_scope = f"chatgpt:{identity.chatgpt_account_id()}"
    for i in range(0, len(changed), batch_files):
        batch = changed[i : i + batch_files]
        lines = []
        per_file: list[tuple[str, float, int, int, str | None]] = []
        read: list[tuple[str, Path]] = []
        for source, path, st in batch:
            drops: Counter = Counter()
            raw: dict = {}
            try:
                file_lines = list(parse(source, path, drops, raw))
            except OSError:
                # Locked or vanished: no checkpoint, so the next sync retries it.
                result.unreadable += 1
                continue
            conn.execute("DELETE FROM raw_lines WHERE path = ?", (str(path),))
            conn.executemany(
                "INSERT INTO raw_lines VALUES (?,?,?,?,?,?)",
                [
                    (str(path), source, hr, session, n, tokens)
                    for (hr, session), (n, tokens) in raw.items()
                ],
            )
            if source == CODEX:
                # Plan readings ride in the same rollout. INSERT OR IGNORE keeps a
                # reparse from adding rows.
                try:
                    plan_rows = codex_plan_readings(path, codex_scope)
                except OSError:
                    plan_rows = []
                conn.executemany(
                    "INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?,?,?,?)",
                    plan_rows,
                )
            lines.extend(file_lines)
            read.append((source, path))
            # turn_count is responses, not lines: one response spans several lines.
            per_file.append(
                (
                    str(path),
                    st.st_mtime,
                    st.st_size,
                    len(dedupe(file_lines)[0]),
                    json.dumps(dict(drops), sort_keys=True) if drops else None,
                )
            )
        turns, dropped = dedupe(lines)
        result.duplicates += dropped
        result.turns += len(turns)
        hours |= upsert_turns(conn, turns, tz, cost_of)
        result.prompts += upsert_titles(
            conn, read_titles([(s, p) for s, p in read if s in CLAUDE_SOURCES])
        )
        stamp = datetime.now(UTC).isoformat()
        conn.executemany(
            "INSERT OR REPLACE INTO files (path, mtime, size, parsed_at, turn_count, dropped)"
            " VALUES (?,?,?,?,?,?)",
            [(path, mtime, size, stamp, n, d) for path, mtime, size, n, d in per_file],
        )
        result.parsed += len(read)
        conn.commit()
        if progress:
            progress(result.parsed, len(changed))

    # Copilot's plan readings live in its entitlement cache, not in a session
    # file, so they are read on every sync. Rows are keyed by time.
    from .ingest.copilot import plan_readings as copilot_plan_readings

    conn.executemany(
        "INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?,?,?,?)",
        copilot_plan_readings(),
    )
    if full_rebuild:
        rebuild_rollup(conn)
    elif hours:
        rebuild_rollup(conn, hours)
    if import_history(conn) or full_rebuild or hours:
        # Report caches are keyed on this, so an unchanged store keeps them.
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('generation', ?)",
            (str(generation(conn) + 1),),
        )
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)",
        (datetime.now(UTC).isoformat(),),
    )
    return result


def upsert_titles(conn, titles) -> int:
    """Keep a session's title unless a better or equal kind arrives."""
    rows = [
        (t["session"], t["kind"], t["rank"], t.get("ts"), t["text"])
        for t in titles
        if t.get("text")
    ]
    conn.executemany(
        "INSERT INTO titles (session, kind, rank, ts, text) VALUES (?,?,?,?,?)"
        " ON CONFLICT(session) DO UPDATE SET kind = excluded.kind, rank = excluded.rank,"
        " ts = excluded.ts, text = excluded.text WHERE excluded.rank >= titles.rank",
        rows,
    )
    return len(rows)


def dropped_records(conn) -> dict[str, int]:
    """Records skipped across all read files, by reason."""
    totals: dict[str, int] = {}
    for (blob,) in conn.execute("SELECT dropped FROM files WHERE dropped IS NOT NULL"):
        for reason, n in json.loads(blob).items():
            totals[reason] = totals.get(reason, 0) + int(n)
    return totals


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


# Environments that leave no local log. A session that ran on this machine
# (Remote Control bridges one to claude.ai) is already counted from its log.
_CLOUD_ENVIRONMENTS = {"anthropic_cloud"}


def _count(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"not a token count: {value!r}")
    return int(value)


def local_session_ids(conn: sqlite3.Connection) -> set[str]:
    """Include original IDs so foreign-machine turns also suppress harvested totals."""
    ids = set()
    for session, machine in conn.execute(
        "SELECT DISTINCT session,machine_id FROM turns"
    ):
        ids.add(session)
        prefix = f"vwx:{machine}:"
        if session.startswith(prefix):
            ids.add(session[len(prefix) :])
    return ids


def upsert_cloud_sessions(conn, payload) -> dict[str, int]:
    """Ingest a session listing from the Claude Code session API.

    Returns counts: written; skipped (no usage block: an unstarted session is
    not free work); rejected_no_id (no key to dedup on, A-032);
    skipped_environment (ran locally, so its log already counts it).
    A session with usage but no cost_usd is stored unpriced, not at $0 (A-027).
    """
    from .ingest import cloud

    counts = {"written": 0, "skipped": 0, "rejected_no_id": 0, "skipped_environment": 0}
    local_ids = local_session_ids(conn)
    rows = []
    for s in cloud.parse(payload):
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            counts["rejected_no_id"] += 1
            continue
        meta = (
            s.get("external_metadata")
            if isinstance(s.get("external_metadata"), dict)
            else {}
        )
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
        if not usage:
            counts["skipped"] += 1
            continue
        tags = [str(t) for t in s.get("tags") or [] if isinstance(t, (str, int))]
        cowork = any(t.startswith("cowork") for t in tags)
        environment = s.get("environment_kind")
        if sid in local_ids or (
            environment is not None
            and environment not in _CLOUD_ENVIRONMENTS
            and not cowork
        ):
            counts["skipped_environment"] += 1
            continue
        ctx = (
            s.get("session_context")
            if isinstance(s.get("session_context"), dict)
            else {}
        )
        origin = str(s.get("origin") or "")
        surface = "cowork" if cowork else _SURFACE.get(origin, "claude-code")
        cu = (
            meta.get("context_usage")
            if isinstance(meta.get("context_usage"), dict)
            else {}
        )
        cost = usage.get("cost_usd")
        try:
            row = (
                sid,
                s.get("title") if isinstance(s.get("title"), str) else None,
                origin,
                surface,
                _repo_name(ctx),
                (ctx.get("model") or meta.get("model")),
                s.get("created_at"),
                s.get("updated_at"),
                _count(usage.get("input_tokens")),
                _count(usage.get("cache_write_tokens")),
                _count(usage.get("cache_read_tokens")),
                _count(usage.get("output_tokens")),
                None if cost is None else float(cost),
                cu.get("used_tokens"),
                cu.get("max_tokens"),
                1,
                json.dumps(s, separators=(",", ":"))[:20000],
            )
        except (TypeError, ValueError):
            counts["skipped"] += 1
            continue
        rows.append(row)
        counts["written"] += 1
    conn.executemany(
        "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    if rows:
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_harvest', ?)",
            (datetime.now(UTC).isoformat(),),
        )
    return counts


def sessions(
    conn,
    limit: int = 40,
    *,
    cursor: str | None = None,
    source: str | None = None,
    project: str | list[str] | None = None,
    model: str | None = None,
    labels: dict[str, str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tz: tzinfo = UTC,
    search: str | None = None,
) -> list[dict]:
    """Sessions across every surface, local and harvested, newest first.

    Shared by the CLI's `sessions` command and the API's `/api/sessions`, so
    the two never drift on what a "session" row looks like. New cursors include
    timestamp and ID to preserve ties; legacy timestamp cursors remain valid.
    Local totals include matching turns. Cloud totals cannot be split by day
    and are selected by their start date in the report timezone.
    """
    scope_sql, local_args = source_clause(source)
    local_clauses = [scope_sql]
    if project is not None:
        from .projects import clause

        sql, values = clause("project", project)
        local_clauses.append(sql)
        local_args.extend(values)
    if model:
        local_clauses.append("model = ?")
        local_args.append(model)
    for day, op, offset in ((date_from, ">=", 0), (date_to, "<", 1)):
        if day:
            boundary = datetime.combine(day, time.min, tz) + timedelta(days=offset)
            local_clauses.append(f"ts {op} ?")
            local_args.append(boundary.astimezone(UTC).isoformat())
    local = conn.execute(
        "SELECT session id, MIN(ts) started, MAX(ts) ended, project,"
        "  GROUP_CONCAT(DISTINCT model) model, COUNT(*) n, SUM(cost) cost,"
        "  SUM(cost IS NULL) unpriced_turns,"
        "  SUM(input+cache_5m+cache_1h+cache_read+output) tokens, source surface"
        f" FROM turns WHERE {' AND '.join(local_clauses)}"
        " GROUP BY session ORDER BY started DESC",
        local_args,
    ).fetchall()

    cloud_sql, cloud_args = source_clause(source, "surface")
    cloud_clauses = ["harvested = 1", cloud_sql]
    if project is not None:
        from .projects import clause

        sql, values = clause("project", project)
        cloud_clauses.append(sql)
        cloud_args.extend(values)
    if model:
        cloud_clauses.append("model = ?")
        cloud_args.append(model)
    cloud = conn.execute(
        "SELECT id, title, started, ended, project, model, surface, context_used, context_max,"
        "  cost, input+cache_write+cache_read+output tokens"
        f" FROM sessions WHERE {' AND '.join(cloud_clauses)}"
        " ORDER BY started DESC",
        cloud_args,
    ).fetchall()

    titles = {
        r["session"]: r["text"]
        for r in conn.execute("SELECT session, text FROM titles")
    }

    rows = []
    local_ids = local_session_ids(conn)
    for r in cloud:
        if r["id"] in local_ids:
            continue
        if date_from or date_to:
            try:
                stamp = datetime.fromisoformat(r["started"] or "")
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                day = stamp.astimezone(tz).date()
            except (ValueError, TypeError):
                continue
            if (date_from and day < date_from) or (date_to and day > date_to):
                continue
        rows.append(
            {
                "id": r["id"],
                "title": r["title"] or r["id"][:24],
                "surface": r["surface"],
                "project": r["project"] or "-",
                "model": r["model"],
                "started": r["started"],
                "ended": r["ended"],
                "tokens": r["tokens"] or 0,
                "cost": r["cost"] or 0.0,
                "harvested": True,
                # No cost_usd from the session API: unpriced, not free (A-027).
                "unpriced_turns": int(r["cost"] is None),
                "context_used": r["context_used"],
                "context_max": r["context_max"],
            }
        )
    for r in local:
        title = titles.get(r["id"], "") or r["id"][:24]
        rows.append(
            {
                "id": r["id"],
                "title": title,
                "surface": r["surface"],
                "project": r["project"] or "-",
                "model": r["model"],
                "started": r["started"],
                "ended": r["ended"],
                "tokens": r["tokens"] or 0,
                "cost": r["cost"] or 0.0,
                "harvested": False,
                "unpriced_turns": r["unpriced_turns"],
            }
        )
    if labels is not None:
        for row in rows:
            row["project"] = labels.get(row["project"], row["project"])

    def sort_key(row):
        return (row["started"] or "", row["id"])

    rows.sort(key=sort_key, reverse=True)
    if search:
        needle = search.casefold()
        rows = [
            r
            for r in rows
            if any(
                needle in str(r.get(key) or "").casefold()
                for key in ("title", "project", "model")
            )
        ]
    if cursor:
        if cursor.startswith("["):
            boundary = json.loads(cursor)
            if (
                not isinstance(boundary, list)
                or len(boundary) != 2
                or not all(isinstance(v, str) for v in boundary)
            ):
                raise ValueError("invalid session cursor")
            rows = [r for r in rows if sort_key(r) < tuple(boundary)]
        else:
            rows = [r for r in rows if (r["started"] or "") < cursor]
    for row in rows[:limit]:
        row["cursor"] = json.dumps(sort_key(row), separators=(",", ":"))
    return rows[:limit]


def session_detail(conn, session_id: str) -> dict | None:
    """One session's metadata plus its turns. None if the id is unknown.

    Shape is the same whether the session is local (has turns) or harvested
    (totals only, from the session API) so a caller does not need to branch.
    """
    turns = conn.execute(
        "SELECT msg_id, request_id, ts, day, source, project, model, input,"
        "  cache_5m, cache_1h, cache_read, output, thinking, web_search,"
        "  sidechain, fast, geo, cost FROM turns WHERE session = ? ORDER BY ts",
        (session_id,),
    ).fetchall()
    if not turns:
        # Old cloud deep links resolve to imported local evidence after overlap suppression.
        turns = conn.execute(
            "SELECT msg_id, request_id, ts, day, source, project, model, input,"
            " cache_5m, cache_1h, cache_read, output, thinking, web_search,"
            " sidechain, fast, geo, cost FROM turns"
            " WHERE session = 'vwx:' || machine_id || ':' || ? ORDER BY ts",
            (session_id,),
        ).fetchall()
    if turns:
        title = conn.execute(
            "SELECT text FROM titles WHERE session = ?", (session_id,)
        ).fetchone()
        first, last = turns[0], turns[-1]
        tokens = sum(
            t["input"] + t["cache_5m"] + t["cache_1h"] + t["cache_read"] + t["output"]
            for t in turns
        )
        cost = sum(t["cost"] or 0 for t in turns)
        models = sorted({t["model"] for t in turns})
        return {
            "id": session_id,
            "harvested": False,
            "title": (title["text"] if title else None) or session_id[:24],
            "surface": first["source"],
            "project": first["project"],
            "model": ",".join(models),
            "started": first["ts"],
            "ended": last["ts"],
            "tokens": tokens,
            "cost": cost,
            "unpriced_turns": sum(t["cost"] is None for t in turns),
            "turns": [dict(t) for t in turns],
        }
    cloud = conn.execute(
        "SELECT * FROM sessions WHERE id = ? AND harvested = 1", (session_id,)
    ).fetchone()
    if cloud is None:
        return None
    row = dict(cloud)
    return {
        "id": row["id"],
        "harvested": True,
        "title": row["title"] or row["id"][:24],
        "surface": row["surface"],
        "project": row["project"],
        "model": row["model"],
        "started": row["started"],
        "ended": row["ended"],
        "tokens": (row["input"] or 0)
        + (row["cache_write"] or 0)
        + (row["cache_read"] or 0)
        + (row["output"] or 0),
        "cost": row["cost"] or 0.0,
        "context_used": row["context_used"],
        "context_max": row["context_max"],
        "turns": [],
    }


def summary(conn) -> dict:
    def one(sql, *args):
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row else {}

    turns = one(
        "SELECT COUNT(*) n, MIN(day) lo, MAX(day) hi, SUM(cost) cost FROM turns"
    )
    cloud = one("SELECT COUNT(*) n, SUM(cost) cost FROM sessions WHERE harvested=1")
    by_surface = [
        dict(r)
        for r in conn.execute(
            "SELECT surface, COUNT(*) n, SUM(cost) cost FROM sessions WHERE harvested=1"
            " GROUP BY surface ORDER BY cost DESC"
        )
    ]
    prompts = one("SELECT COUNT(*) n FROM titles")  # the API key predates titles
    last = one("SELECT value FROM meta WHERE key='last_harvest'")
    synced = one("SELECT value FROM meta WHERE key='last_sync'")
    return {
        "turns": turns,
        "cloud": cloud,
        "cloud_by_surface": by_surface,
        "prompts": prompts,
        "last_harvest": last.get("value"),
        "last_sync": synced.get("value"),
    }


RECONCILIATION_REASONS = [
    (
        "Claude's Stats count every log line. Claude Code writes one line per content block "
        "and repeats the response's usage on each, so a response's tokens are counted "
        "several times. vibewatt counts each response once."
    ),
    (
        "Stats count input and output tokens only. vibewatt's totals also include cache "
        "reads and writes, which are most of the tokens and most of the cost."
    ),
    (
        "Stats count messages: your lines and Claude's lines, outside subagents. vibewatt "
        "counts responses: one per API call, subagents included."
    ),
    ("Stats tokens include subagent work in the same total."),
    (
        "Stats cover Claude Code only (not Cowork) and may be a snapshot from when the "
        "app last refreshed."
    ),
]

SESSION_DEFINITION = (
    "A session is one Claude Code or Cowork session id with at least one billable "
    "response kept after dedup. Sessions with only your messages, or only local "
    "stand-in responses, are not counted, so this can be lower than Claude's own count."
)


def reconciliation(
    conn, tz, date_from: date | None = None, date_to: date | None = None
) -> dict:
    """vibewatt's deduped figures next to the figures Claude's Stats would show.

    Claude Code only, because the Stats exclude Cowork. The Stats-equivalent
    figures are bucketed by UTC hour, so a day boundary in a zone with a
    half-hour offset can be off by those minutes.
    """
    source = "claude-code"
    stats = {"messages": 0, "tokens": 0, "sessions": 0}
    sessions: set[str] = set()
    for hr, session, messages, tokens in conn.execute(
        "SELECT hr, session, SUM(messages), SUM(tokens) FROM raw_lines WHERE source = ?"
        " GROUP BY hr, session",
        (source,),
    ):
        day = datetime.fromisoformat(f"{hr}:00:00+00:00").astimezone(tz).date()
        if (date_from and day < date_from) or (date_to and day > date_to):
            continue
        stats["messages"] += messages
        stats["tokens"] += tokens
        sessions.add(session)
    stats["sessions"] = len(sessions)
    where, args = ["source = ?"], [source]
    if date_from:
        where.append("day >= ?")
        args.append(date_from.isoformat())
    if date_to:
        where.append("day <= ?")
        args.append(date_to.isoformat())
    row = conn.execute(
        "SELECT COALESCE(SUM(responses), 0), COALESCE(SUM(input + output), 0),"
        " COALESCE(SUM(input + cache_5m + cache_1h + cache_read + output), 0),"
        f" COUNT(DISTINCT session) FROM rollup WHERE {' AND '.join(where)}",
        args,
    ).fetchone()
    deduped = {
        "responses": row[0],
        "input_output_tokens": row[1],
        "all_tokens": row[2],
        "sessions": row[3],
    }
    ratio = (
        stats["tokens"] / deduped["input_output_tokens"]
        if deduped["input_output_tokens"]
        else None
    )
    return {
        "source": source,
        "deduped": deduped,
        "stats_equivalent": stats,
        "token_ratio": ratio,
        "reasons": RECONCILIATION_REASONS,
        "session_definition": SESSION_DEFINITION,
    }


def coverage(conn, min_gap_days: int = 7) -> dict:
    """What the store covers: files per source and spans with no local turn."""
    from .sources import CLAUDE_CODE, COWORK

    sources = []
    for source in (CLAUDE_CODE, COWORK):
        files = conn.execute(
            "SELECT COUNT(*) FROM files WHERE path LIKE ?",
            ("%audit.jsonl" if source == COWORK else "%.jsonl",),
        ).fetchone()[0]
        if source == CLAUDE_CODE:
            files -= conn.execute(
                "SELECT COUNT(*) FROM files WHERE path LIKE '%audit.jsonl'"
            ).fetchone()[0]
        lo, hi = conn.execute(
            "SELECT MIN(day), MAX(day) FROM turns WHERE source = ?", (source,)
        ).fetchone()
        sources.append(
            {"source": source, "files": files, "first_day": lo, "last_day": hi}
        )
    days = [
        date.fromisoformat(r[0])
        for r in conn.execute("SELECT DISTINCT day FROM turns ORDER BY day")
    ]
    gaps = []
    for prev, cur in pairwise(days):
        missing = (cur - prev).days - 1
        if missing >= min_gap_days:
            gaps.append(
                {
                    "start": (prev + timedelta(days=1)).isoformat(),
                    "end": (cur - timedelta(days=1)).isoformat(),
                    "days": missing,
                }
            )
    return {"sources": sources, "gaps": gaps, "dropped_records": dropped_records(conn)}


def dismissal_key(finding: dict, scope: str) -> str:
    """A session finding is dismissed wherever it appears; others per selection (A-091)."""
    if finding.get("session_id"):
        return f"{finding['rule']}|{finding['subject']}"
    return f"{finding['rule']}|{finding['subject']}|{scope}"


def _with_dismissals(conn, rows) -> list[dict]:
    dismissed = {r[0] for r in conn.execute("SELECT key FROM dismissals")}
    out = []
    for r in rows:
        item = json.loads(r["detail_json"])
        key = dismissal_key(item, r["scope"]) if "rule" in item else None
        out.append(
            dict(
                item,
                dismissed=bool(r["dismissed"]) or key in dismissed,
                created_at=r["created_at"],
            )
        )
    return out


def active_findings(conn, scope: str) -> list[dict]:
    return _with_dismissals(
        conn,
        conn.execute("SELECT * FROM findings WHERE scope = ? AND active = 1", (scope,)),
    )


def save_findings(
    conn: sqlite3.Connection, scope: str, findings: list[dict], *, prune: bool = False
) -> list[dict]:
    """Replace the active snapshot. Dismissals live in their own table.

    With `prune`, rows of earlier snapshots are deleted instead of kept
    inactive: every filter selection used to leave a snapshot behind for good
    (A-092). Alerts keep theirs, because a fired alert must stay known.
    """
    now = datetime.now(UTC).isoformat()
    if prune:
        conn.execute("DELETE FROM findings WHERE scope = ?", (scope,))
    conn.execute("UPDATE findings SET active = 0 WHERE scope = ?", (scope,))
    for finding in findings:
        conn.execute(
            "INSERT INTO findings (id, scope, kind, severity, day, subject, detail_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "severity=excluded.severity, day=excluded.day, detail_json=excluded.detail_json, active=1",
            (
                finding["id"],
                scope,
                finding["kind"],
                finding["severity"],
                finding["day"],
                finding["subject"],
                json.dumps(finding),
                now,
            ),
        )
    return active_findings(conn, scope)


def dismiss_finding(conn: sqlite3.Connection, finding_id: str, dismissed: bool) -> bool:
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if row is None:
        return False
    item = json.loads(row["detail_json"])
    if "rule" in item:
        key = dismissal_key(item, row["scope"])
        if dismissed:
            conn.execute(
                "INSERT OR REPLACE INTO dismissals VALUES (?, ?)",
                (key, datetime.now(UTC).isoformat()),
            )
        else:
            conn.execute("DELETE FROM dismissals WHERE key = ?", (key,))
    conn.execute(
        "UPDATE findings SET dismissed = ? WHERE id = ?", (int(dismissed), finding_id)
    )
    return True


def finding(conn: sqlite3.Connection, finding_id: str) -> dict | None:
    rows = _with_dismissals(
        conn, conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,))
    )
    return rows[0] if rows else None
