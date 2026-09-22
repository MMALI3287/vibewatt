"""d6 probes: localhost web security (DNS rebinding, CSRF, CORS)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from vibewatt import config as configmod
from vibewatt.api import create_app

EVIL = {"Host": "evil.example:8777", "Origin": "http://evil.example:8777"}
XORIGIN = {"Origin": "https://evil.example"}


def _client(**extra):
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    cfg.update(extra)
    return TestClient(create_app(cfg)), cfg


def test_dns_rebinding_host_header_is_accepted(logs, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    client, _ = _client()
    client.post("/api/sync")
    rows = client.get("/api/sessions", headers=EVIL)
    print("sessions via evil host:", rows.status_code, [r.get("title") for r in rows.json()][:3])
    assert rows.status_code == 200 and rows.json()
    exp = client.get("/api/export", params={"format": "json"}, headers=EVIL)
    print("export via evil host:", exp.status_code, len(exp.text))
    assert exp.status_code == 200
    legacy = client.get("/", headers=EVIL)
    print("legacy page via evil host:", legacy.status_code)
    assert legacy.status_code == 200


def test_no_permissive_cors(logs):
    client, _ = _client()
    pre = client.options("/api/sessions", headers={
        **XORIGIN, "Access-Control-Request-Method": "GET"})
    get = client.get("/api/summary", headers=XORIGIN)
    print("preflight:", pre.status_code, dict(pre.headers))
    assert "access-control-allow-origin" not in pre.headers
    assert "access-control-allow-origin" not in get.headers


def test_csrf_harvest_text_plain_writes_store(logs):
    client, _ = _client()
    before = client.get("/api/wrapped", params={"year": 2026}).json()
    body = json.dumps([{"id": "evil-1", "title": "injected by evil.example",
                        "created_at": "2026-09-15T00:00:00Z",
                        "external_metadata": {"usage": {"input_tokens": 5_000_000_000,
                                                        "cost_usd": 1_000_000.0}}}])
    resp = client.post("/api/harvest", content=body,
                       headers={**XORIGIN, "Content-Type": "text/plain;charset=UTF-8"})
    print("harvest text/plain:", resp.status_code, resp.text)
    assert resp.status_code == 200 and resp.json()["written"] == 1
    after = client.get("/api/wrapped", params={"year": 2026}).json()
    print("wrapped stored_cost before/after:", before["stored_cost_usd"], after["stored_cost_usd"])
    assert after["stored_cost_usd"] - before["stored_cost_usd"] >= 999_999
    titles = [r["title"] for r in client.get("/api/sessions").json()]
    assert "injected by evil.example" in titles


def test_dismiss_rejects_simple_requests_but_not_rebinding(logs):
    client, _ = _client()
    plain = client.post("/api/findings/nope/dismiss", content=b'{"dismissed": true}',
                        headers={**XORIGIN, "Content-Type": "text/plain"})
    raw = client.build_request("POST", "/api/findings/nope/dismiss",
                               content=b'{"dismissed": true}', headers=XORIGIN)
    raw.headers.pop("content-type", None)
    assert "content-type" not in raw.headers
    none = client.send(raw)
    print("dismiss text/plain:", plain.status_code, "| no content-type:", none.status_code, none.text)
    # FastAPI (strict content type) rejects both: dismiss is not simple-request CSRF-able
    assert plain.status_code == 422 and none.status_code == 422
    ok = client.post("/api/findings/nope/dismiss", json={"dismissed": True}, headers=EVIL)
    print("dismiss json via evil Host (rebinding, same-origin):", ok.status_code, ok.text)
    assert ok.status_code == 404 and "finding not found" in ok.text


def test_csrf_bodyless_posts_run(logs):
    client, _ = _client()
    for path in ("/api/sync", "/api/analysis"):
        r = client.post(path, content=b"", headers={**XORIGIN, "Content-Type": "text/plain"})
        print(path, r.status_code)
        assert r.status_code == 200


def test_csrf_weekly_summary_triggers_paid_call(logs, monkeypatch):
    from vibewatt import weekly

    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode()

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, dict(req.header_items())))
        return Resp()

    monkeypatch.setattr(weekly, "urlopen", fake_urlopen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-FAKEKEY-123")
    client, _ = _client(ai_summary={"enabled": True})
    r = client.post("/api/weekly-summary", content=b"",
                    headers={**EVIL, "Content-Type": "text/plain"})
    print("weekly via evil host:", r.status_code, r.json()["status"], "calls:", len(calls))
    assert r.status_code == 200 and len(calls) == 1
    assert "sk-test-FAKEKEY-123" not in r.text
    assert calls[0][0] == "https://api.anthropic.com/v1/messages"
