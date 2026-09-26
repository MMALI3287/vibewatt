"""Time every report endpoint against a synthetic store.

    uv run python scripts/bench_store.py --turns 1000000

Phase 6.5b gate: each endpoint answers in under 300 ms on a 1M-turn store. The
store is written directly (no log files), so this measures reads only.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ENDPOINTS = (
    "/api/summary",
    "/api/daily",
    "/api/hourly",
    "/api/blocks",
    "/api/breakdown/model",
    "/api/breakdown/project",
    "/api/export?format=csv",
    "/api/summary?project=p3",
    "/api/summary?from=2026-06-01&to=2026-06-30",
)


def build(path: Path, n: int) -> None:
    from vibewatt import store

    rng = random.Random(7)
    start = datetime(2025, 9, 1, tzinfo=UTC)
    models = [
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
        "claude-fable-5-1",
    ]
    with store.connect(path) as conn:
        rows = []
        session, ts = 0, start
        for i in range(n):
            if i % 50 == 0:  # a 50-response session every so often
                session += 1
                ts = start + timedelta(minutes=rng.randrange(0, 365 * 24 * 60))
            ts += timedelta(seconds=rng.randrange(5, 90))
            rows.append(
                (
                    f"m{i}",
                    f"r{i}",
                    ts.isoformat(),
                    ts.date().isoformat(),
                    "claude-code",
                    f"p{session % 30}",
                    f"s{session}",
                    models[session % 4],
                    10,
                    100,
                    0,
                    5000,
                    rng.randrange(1, 2000),
                    0,
                    0,
                    int(i % 17 == 0),
                    0,
                    None,
                    0.01,
                    "2.1.90",
                    ts.hour,
                )
            )
            if len(rows) == 100_000:
                conn.executemany(
                    f"INSERT INTO turns ({store.TURN_COLUMNS}) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                rows.clear()
        if rows:
            conn.executemany(
                f"INSERT INTO turns ({store.TURN_COLUMNS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        now = datetime.now(UTC)
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('sync_tz', ?)",
            (f"UTC|{now.utcoffset()}",),
        )
        store.rebuild_rollup(conn)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('history_imported', 'bench')")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=1_000_000)
    parser.add_argument("--limit-ms", type=float, default=300.0)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="vibewatt-bench-"))
    os.environ["VIBEWATT_DATA_DIR"] = str(tmp)
    from fastapi.testclient import TestClient

    from vibewatt import store
    from vibewatt.api import create_app

    t0 = time.perf_counter()
    build(store.db_path(), args.turns)
    with store.connect() as conn:
        rollup = conn.execute("SELECT COUNT(*) FROM rollup").fetchone()[0]
    print(
        f"built {args.turns:,} turns ({rollup:,} rollup rows) in "
        f"{time.perf_counter() - t0:.1f}s"
    )

    app = create_app({"offline": True, "quota": False, "timezone": "utc"})
    app.state.synced = True  # measure reads, not a sync of this machine's logs
    # What the server does after every sync: build the unfiltered report.
    t = time.perf_counter()
    app.state.report(
        source="all", date_from=None, date_to=None, project=None, model=None, parts=None
    )
    print(
        f"post-sync warm of the unfiltered report: {(time.perf_counter() - t) * 1000:.0f} ms"
    )
    client = TestClient(app)
    worst = 0.0
    for url in ENDPOINTS:
        # The first hit is the honest one: a later hit is served from the cache.
        t = time.perf_counter()
        resp = client.get(url)
        first = (time.perf_counter() - t) * 1000
        assert resp.status_code == 200, (url, resp.text[:200])
        worst = max(worst, first)
        print(f"  {first:7.1f} ms  {url}")
    ok = worst < args.limit_ms
    print(
        f"worst {worst:.1f} ms, limit {args.limit_ms:.0f} ms: {'PASS' if ok else 'FAIL'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
