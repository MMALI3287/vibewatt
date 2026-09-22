"""d2 audit probe: cost of one Overview load when every endpoint reparses all logs."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from vibewatt.api import create_app


def test_overview_reparse_cost(tmp_path, capsys):
    base = tmp_path / "claude" / "projects"
    n = 0
    for p in range(40):
        d = base / f"proj{p}"
        d.mkdir(parents=True)
        lines = []
        for i in range(1000):
            n += 1
            lines.append(json.dumps({"type": "assistant", "requestId": f"r{p}-{i}",
                                     "timestamp": f"2026-0{1 + i % 9}-{10 + i % 18:02d}T0{i % 10}:00:00Z",
                                     "sessionId": f"s{p}-{i // 100}", "cwd": f"/w/proj{p}",
                                     "message": {"id": f"m{p}-{i}", "model": "claude-opus-5",
                                                 "usage": {"input_tokens": 10, "output_tokens": 5}}}))
        (d / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    c = TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}))
    t0 = time.perf_counter()
    c.post("/api/sync")
    t_sync = time.perf_counter() - t0
    timings = {}
    for path in ("/api/summary", "/api/quota", "/api/blocks", "/api/sessions", "/api/health"):
        t0 = time.perf_counter()
        assert c.get(path).status_code == 200
        timings[path] = round(time.perf_counter() - t0, 3)
    print(f"{n} responses; first sync {t_sync:.2f}s; per-request seconds after sync:", timings)
