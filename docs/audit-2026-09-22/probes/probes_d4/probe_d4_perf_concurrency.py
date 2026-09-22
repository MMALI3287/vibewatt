"""d4 probes: GET /api/findings performance at ~200k turns and concurrent GETs."""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from vibewatt import store
from vibewatt.api import create_app

UTC = timezone.utc
COLS = ["msg_id", "request_id", "ts", "day", "source", "project", "session", "model",
        "input", "cache_5m", "cache_1h", "cache_read", "output", "thinking", "web_search",
        "sidechain", "fast", "geo", "cost"]


def seed(n_turns: int, per_session: int = 100, reads_per_session: int = 20):
    rng = random.Random(1)
    start = datetime.now(UTC) - timedelta(days=365)
    rows, reads = [], []
    n_sessions = n_turns // per_session
    for s in range(n_sessions):
        t0 = start + timedelta(days=rng.randrange(365), hours=rng.randrange(24))
        project = f"proj{s % 25}"
        model = rng.choice(["claude-opus-5", "claude-sonnet-4-5", "claude-haiku-4-5"])
        low_cache = rng.random() < 0.2
        for i in range(per_session):
            ts = t0 + timedelta(minutes=2 * i)
            rows.append((f"m{s}-{i}", f"r{s}-{i}", ts.isoformat(), ts.date().isoformat(),
                         "claude-code", project, f"sess{s}", model,
                         5000 if low_cache else 50, 0, 2000 if i == 0 else 0,
                         0 if low_cache else 40_000, rng.randrange(20, 2000), 0, 0,
                         int(rng.random() < 0.3), int(rng.random() < 0.02), None,
                         round(rng.random() * 0.2, 4)))
        for j in range(reads_per_session):
            reads.append((f"sess{s}", f"tool{s}-{j}", (t0 + timedelta(minutes=j)).isoformat(),
                          "claude-code", project, model, f"h{s}-{j % 6}"))
    with store.connect() as conn:
        conn.executemany(f"INSERT INTO turns ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})", rows)
        conn.executemany("INSERT INTO tool_reads VALUES (?,?,?,?,?,?,?)", reads)
    return len(rows), len(reads), n_sessions


def client():
    return TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}))


def test_perf_200k_turns():
    t = time.perf_counter()
    n, r, s = seed(200_000)
    print(f"\nseeded {n} turns {r} reads {s} sessions in {time.perf_counter() - t:.1f}s")
    c = client()
    timings = []
    for label, url in [("cold", "/api/findings"), ("warm", "/api/findings"),
                       ("project filter", "/api/findings?project=proj3"),
                       ("kind filter", "/api/findings?kind=anomaly")]:
        t = time.perf_counter()
        resp = c.get(url)
        dt = time.perf_counter() - t
        timings.append((label, dt))
        print(f"{label}: {resp.status_code} {dt:.2f}s findings={len(resp.json()['findings'])}")
    with store.connect() as conn:
        print("findings rows:", conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0])


def test_concurrent_gets_no_lock_errors_or_duplicates():
    seed(40_000)
    errors, statuses = [], []
    barrier = threading.Barrier(8)

    def worker(url):
        c = client()
        barrier.wait()
        try:
            resp = c.get(url)
            statuses.append(resp.status_code)
            if resp.status_code != 200:
                errors.append(resp.text[:200])
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc)[:200])

    urls = ["/api/findings"] * 4 + ["/api/findings?project=proj1", "/api/findings?project=proj2",
                                    "/api/findings?kind=cache", "/api/findings"]
    threads = [threading.Thread(target=worker, args=(u,)) for u in urls]
    t = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    print(f"\nconcurrent statuses={statuses} errors={errors} in {time.perf_counter() - t:.2f}s")
    with store.connect() as conn:
        dup = conn.execute("SELECT id, COUNT(*) FROM findings GROUP BY id HAVING COUNT(*) > 1").fetchall()
        active = conn.execute("SELECT scope, COUNT(*) FROM findings WHERE active=1 GROUP BY scope").fetchall()
    print("duplicate ids:", len(dup), "active per scope:", [tuple(a)[1] for a in active])
    assert not errors and not dup


def test_findings_get_during_long_write_transaction():
    """A sync holds one write transaction until it finishes; read-only GETs still work."""
    seed(2_000)
    import sqlite3
    held = threading.Event()
    release = threading.Event()

    def writer():
        conn = sqlite3.connect(str(store.db_path()))
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO meta VALUES ('probe_lock', '1')")
        held.set()
        release.wait(20)
        conn.rollback()
        conn.close()

    th = threading.Thread(target=writer)
    th.start()
    held.wait(5)
    c = TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}),
                   raise_server_exceptions=False)
    t = time.perf_counter()
    daily = c.get("/api/daily")
    t_daily = time.perf_counter() - t
    t = time.perf_counter()
    resp = c.get("/api/findings")
    t_find = time.perf_counter() - t
    release.set()
    th.join()
    print(f"\nwhile write lock held: /api/daily {daily.status_code} in {t_daily:.2f}s; "
          f"/api/findings {resp.status_code} in {t_find:.2f}s body={resp.text[:80]!r}")
