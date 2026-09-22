"""v19: independent repro of d6-security#2 (harvest CSRF via simple request)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from vibewatt import config as configmod
from vibewatt.api import create_app

XO = {"Origin": "https://evil.example"}


def _client():
    cfg = configmod.load()
    cfg.update(offline=True, quota=False)
    return TestClient(create_app(cfg))


def _sess(sid, title, cost):
    return {"id": sid, "title": title, "created_at": "2026-09-15T00:00:00Z",
            "external_metadata": {"usage": {"input_tokens": 10, "cost_usd": cost}}}


def test_simple_request_writes_and_overwrites():
    c = _client()
    ok = c.post("/api/harvest", json=[_sess("s1", "legit", 2.5)])
    print("legit json:", ok.status_code, ok.text)
    body = json.dumps([_sess("s1", "overwritten by evil", 999999.0)])
    r = c.post("/api/harvest", content=body,
               headers={**XO, "Content-Type": "text/plain;charset=UTF-8"})
    print("xorigin text/plain:", r.status_code, r.text, "ACAO:", r.headers.get("access-control-allow-origin"))
    rows = [x for x in c.get("/api/sessions").json() if x.get("id") == "s1" or x.get("title", "").startswith(("legit", "overwritten"))]
    print("rows after:", [(x.get("title"), x.get("cost") or x.get("cost_usd")) for x in rows])
    w = c.get("/api/wrapped", params={"year": 2026}).json()
    print("wrapped stored_cost_usd:", w.get("stored_cost_usd"))
    raw = c.build_request("POST", "/api/harvest", content=json.dumps([_sess("s2", "noct", 1.0)]).encode(), headers=XO)
    raw.headers.pop("content-type", None)
    r2 = c.send(raw)
    print("xorigin no content-type:", r2.status_code, r2.text)
    d = c.post("/api/findings/x/dismiss", content=b'{"dismissed":true}', headers={**XO, "Content-Type": "text/plain"})
    print("dismiss text/plain (typed body control):", d.status_code)
    assert r.status_code == 200 and r.json()["written"] == 1
