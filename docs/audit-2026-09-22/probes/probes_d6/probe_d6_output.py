"""d6 probes: output encoding (legacy HTML page, CSV export)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ccburn import config as configmod
from ccburn.api import create_app


def _write(tmp_path, cwd, model):
    path = tmp_path / "claude" / "projects" / "x" / "s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"type": "assistant", "requestId": "r1", "timestamp": "2026-09-15T20:00:00Z",
           "sessionId": "s1", "cwd": cwd,
           "message": {"id": "m1", "model": model,
                       "usage": {"input_tokens": 10, "output_tokens": 5}}}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def _client():
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    return TestClient(create_app(cfg))


def test_legacy_page_embeds_unescaped_markup(tmp_path):
    _write(tmp_path, "/work/<img src=x onerror=alert(1)>",
           "claude</script><script>alert(2)</script>")
    html = _client().get("/").text
    breakout = "</script><script>alert(2)</script>"
    print("script breakout present:", breakout in html)
    print("img payload present:", "<img src=x onerror=alert(1)>" in html)
    print("innerHTML td sink present:", "<td>${r.name}</td>" in html)
    assert breakout in html
    assert "<img src=x onerror=alert(1)>" in html
    assert "<td>${r.name}</td>" in html


def test_csv_export_formula_cells(tmp_path):
    _write(tmp_path, "/work/demo", "=HYPERLINK(1)")
    csv = _client().get("/api/export", params={"format": "csv"}).text
    print(csv)
    assert any(cell.startswith("=") for line in csv.splitlines()[1:] for cell in line.split(","))
