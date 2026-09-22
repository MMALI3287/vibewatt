"""d10: which GET endpoints need the write lock (fail while a sync holds it)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from vibewatt import store
from vibewatt.api import create_app


def test_gets_while_writer_holds_lock(tmp_path):
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "demo"
    d.mkdir(parents=True)
    lines = [json.dumps({
        "type": "assistant", "requestId": f"r{i}", "timestamp": f"2026-09-15T20:{i:02d}:00Z",
        "sessionId": "s1", "cwd": "/work/demo",
        "message": {"id": f"m{i}", "model": "claude-opus-5",
                    "usage": {"input_tokens": 10, "output_tokens": 5 if i < 55 else 50000}}})
        for i in range(60)]
    (d / "a.jsonl").write_text("\n".join(lines) + "\n")
    client = TestClient(create_app({"offline": True, "quota": False}),
                        raise_server_exceptions=False)
    assert client.post("/api/sync").status_code == 200
    # a second process-like writer, as POST /api/sync holds it for the whole parse
    lock = sqlite3.connect(str(store.db_path()), isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    out = {}
    try:
        for url in ["/api/health", "/api/sessions", "/api/sessions/s1", "/api/session-facets",
                    "/api/findings", "/api/alerts", "/api/wrapped?year=2026", "/api/summary"]:
            t0 = time.perf_counter()
            code = client.get(url).status_code
            out[url] = (code, round(time.perf_counter() - t0, 1))
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    print("under write lock", out)
    assert all(code == 200 for code, _ in out.values()), out
