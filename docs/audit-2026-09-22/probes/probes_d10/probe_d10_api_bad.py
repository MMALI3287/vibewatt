"""d10: one wrong-typed line in one log vs the dashboard endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from vibewatt.api import create_app


def _line(i, usage):
    return json.dumps({
        "type": "assistant", "requestId": f"r{i}", "timestamp": "2026-09-15T20:00:00Z",
        "sessionId": "s1", "cwd": "/work/demo",
        "message": {"id": f"m{i}", "model": "claude-opus-5", "usage": usage}})


def test_one_bad_line_vs_endpoints(tmp_path):
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "demo"
    d.mkdir(parents=True)
    (d / "good.jsonl").write_text(_line(1, {"input_tokens": 10, "output_tokens": 5}) + "\n")
    (d / "bad.jsonl").write_text(_line(2, {"input_tokens": "n/a", "output_tokens": 5}) + "\n")
    client = TestClient(create_app({"offline": True, "quota": False}),
                        raise_server_exceptions=False)
    codes = {}
    for method, url in [("get", "/api/summary"), ("get", "/api/daily"),
                        ("get", "/api/breakdown/model"), ("get", "/api/blocks"),
                        ("get", "/api/export?format=csv"), ("post", "/api/sync"),
                        ("get", "/api/health"), ("get", "/api/sessions")]:
        codes[url] = getattr(client, method)(url).status_code
    print("codes", codes)
    assert all(c == 200 for c in codes.values()), codes
