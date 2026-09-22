from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ccburn import store
from ccburn.aggregate import cost_of
from ccburn.api import create_app
from ccburn.ingest import discover
from datetime import timezone


def _line(i, usage, **extra):
    msg = {"id": f"m{i}", "model": "claude-opus-5", "usage": usage}
    msg.update(extra)
    return json.dumps({"type": "assistant", "requestId": f"r{i}",
                       "timestamp": "2026-09-15T20:00:00Z", "sessionId": "s1",
                       "cwd": "/work/demo", "message": msg})


BAD = {
    "input_str": {"input_tokens": "n/a", "output_tokens": 5},
    "cache_split_list": {"input_tokens": 1, "cache_creation": {"ephemeral_5m_input_tokens": [1]}},
    "details_str": {"input_tokens": 1, "output_tokens_details": "x"},
    "server_list": {"input_tokens": 1, "server_tool_use": [1]},
    "float_str": {"input_tokens": "1.5"},
}


def _setup(bad):
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "demo"
    d.mkdir(parents=True)
    (d / "a_good.jsonl").write_text(_line(1, {"input_tokens": 10, "output_tokens": 5}) + "\n")
    (d / "z_bad.jsonl").write_text(_line(2, {"input_tokens": 3}) + "\n" + _line(3, bad) + "\n")
    return {"offline": True, "quota": False}


@pytest.mark.parametrize("name", list(BAD))
def test_store_sync_survives_bad_type(name):
    cfg = _setup(BAD[name])
    files = discover(cfg)
    err = None
    try:
        with store.connect() as conn:
            store.sync_files(conn, files, timezone.utc, lambda t: cost_of(t, None))
    except Exception as e:  # noqa: BLE001
        err = repr(e)
    with store.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    print(name, "err=", err, "turns_in_store=", n)
    assert err is None and n >= 2


def test_api_endpoints_with_one_bad_line():
    cfg = _setup(BAD["input_str"])
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    codes = {u: getattr(client, m)(u).status_code for m, u in [
        ("get", "/api/summary"), ("get", "/api/daily"), ("get", "/api/blocks"),
        ("post", "/api/sync"), ("get", "/api/health")]}
    print("codes", codes)
    assert all(c == 200 for c in codes.values()), codes


def test_control_api_ok_without_bad_line():
    cfg = _setup({"input_tokens": 7})
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    codes = {u: client.get(u).status_code for u in ["/api/summary", "/api/daily"]}
    print("control codes", codes)
    assert all(c == 200 for c in codes.values())


def test_api_exception_type():
    cfg = _setup(BAD["input_str"])
    client = TestClient(create_app(cfg), raise_server_exceptions=True)
    with pytest.raises(Exception) as ei:
        client.get("/api/summary")
    print("summary raises", repr(ei.value)[:120])
