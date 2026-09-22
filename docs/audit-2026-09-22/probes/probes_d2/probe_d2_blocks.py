"""d2 audit probes: blocks ordering, export csv totals, session detail turns."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from vibewatt.api import create_app


def _line(rid, ts, mid, inp=100, out=10):
    return json.dumps({"type": "assistant", "requestId": rid, "timestamp": ts, "sessionId": "s",
                       "cwd": "/w/p", "message": {"id": mid, "model": "claude-opus-5",
                       "usage": {"input_tokens": inp, "output_tokens": out}}})


def test_blocks_active_first_and_export_csv(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    stamps = [now - timedelta(days=3), now - timedelta(days=2), now - timedelta(days=1),
              now - timedelta(minutes=10)]
    f = tmp_path / "claude" / "projects" / "p" / "s.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(_line(f"r{i}", t.isoformat().replace("+00:00", "Z"), f"m{i}")
                           for i, t in enumerate(stamps)) + "\n", encoding="utf-8")
    client = TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}))
    blocks = client.get("/api/blocks").json()
    print("blocks order:", [(b["start"][:16], b["is_active"]) for b in blocks])

    summary = client.get("/api/summary").json()["total"]
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export", params={"format": "csv"}).text)))
    csv_resp = sum(int(r["responses"]) for r in rows)
    csv_cost = round(sum(float(r["cost_usd"]) for r in rows), 6)
    print("csv totals", csv_resp, csv_cost, "summary", summary["responses"], summary["cost_usd"])
    assert csv_resp == summary["responses"] and abs(csv_cost - summary["cost_usd"]) < 1e-6

    client.post("/api/sync")
    detail = client.get("/api/sessions/s").json()
    print("detail turns", len(detail["turns"]), "title", detail["title"])
    assert len(detail["turns"]) == 4

    assert blocks and blocks[0]["is_active"], "spec: /api/blocks lists the active window first"
