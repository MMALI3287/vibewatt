"""d5 probes: AI weekly summary (PLAN 7.8). Transport is always mocked."""

from __future__ import annotations

import io
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ccburn import config, weekly
from ccburn.api import create_app

UTC = timezone.utc


def write_logs(tmp_path, when: datetime):
    ts = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        {"type": "user", "sessionId": "SESSION-ID-XYZ", "timestamp": ts, "cwd": "/work/PROJNAME",
         "message": {"role": "user", "content": "SECRET-PROMPT-TEXT please"}},
        {"type": "assistant", "requestId": "r1", "timestamp": ts, "sessionId": "SESSION-ID-XYZ",
         "cwd": "/work/PROJNAME", "message": {"id": "m1", "model": "claude-opus-5", "usage": {
             "input_tokens": 1000, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 0, "output_tokens": 50}}},
        {"type": "last-prompt", "sessionId": "SESSION-ID-XYZ", "timestamp": ts,
         "lastPrompt": "SECRET-PROMPT-TEXT please"},
        {"type": "summary", "sessionId": "SESSION-ID-XYZ", "summary": "SECRET-TITLE-TEXT"},
    ]
    p = tmp_path / "claude" / "projects" / "PROJNAME" / "x.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def no_socket(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_disabled_makes_no_call_and_no_report(monkeypatch, no_socket, tmp_path):
    write_logs(tmp_path, datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setattr(weekly, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("urlopen")))
    monkeypatch.setattr(weekly.cli, "build_report", lambda *a, **k: (_ for _ in ()).throw(AssertionError("report")))
    for opts in ({"enabled": False}, {"enabled": "true"}, {"enabled": 1}, None, "yes"):
        r = weekly.generate({**config.DEFAULTS, "ai_summary": opts}, UTC)
        assert r["status"] == "disabled"


def test_missing_key(monkeypatch, no_socket):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(weekly, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("urlopen")))
    r = weekly.generate({**config.DEFAULTS, "ai_summary": {"enabled": True}}, UTC)
    print("NOKEY", r)
    assert r["status"] == "unavailable"


def capture(monkeypatch, sent):
    def fake(req, timeout=None):
        sent.append((req, timeout))
        return Resp(json.dumps({"content": [{"type": "text", "text": "ok summary"}]}).encode())

    monkeypatch.setattr(weekly, "urlopen", fake)


def test_request_shape_and_payload_privacy(monkeypatch, no_socket, tmp_path):
    now = datetime.now(UTC)
    write_logs(tmp_path, now - timedelta(hours=1))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    sent = []
    capture(monkeypatch, sent)
    # user config shallow-merges: only enabled given -> model falls back to default
    r = weekly.generate({**config.DEFAULTS, "offline": True, "quota": False,
                         "ai_summary": {"enabled": True}}, UTC)
    assert r["status"] == "ready" and r["text"] == "ok summary"
    req, timeout = sent[0]
    body = json.loads(req.data)
    headers = {k.lower(): v for k, v in req.header_items()}
    print("REQ", req.full_url, req.get_method(), timeout, sorted(headers), body["model"], body["max_tokens"])
    print("CONTENT", body["messages"][0]["content"][:400])
    assert req.full_url == "https://api.anthropic.com/v1/messages" and req.get_method() == "POST"
    assert headers["x-api-key"] == "sk-fake"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["content-type"] == "application/json"
    assert body["model"] == "claude-haiku-4-5" and isinstance(body["max_tokens"], int)
    assert body["messages"][0]["role"] == "user"
    blob = req.data.decode()
    for secret in ("SECRET-PROMPT-TEXT", "SECRET-TITLE-TEXT", "SESSION-ID-XYZ", "PROJNAME", "claude-opus-5"):
        assert secret not in blob, secret
    content = json.loads(body["messages"][0]["content"])
    assert content["total"]["input"] == 1000


def test_project_names_opt_in(monkeypatch, no_socket, tmp_path):
    write_logs(tmp_path, datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    sent = []
    capture(monkeypatch, sent)
    weekly.generate({**config.DEFAULTS, "offline": True, "quota": False,
                     "ai_summary": {"enabled": True, "include_project_names": True}}, UTC)
    assert "PROJNAME" in sent[0][0].data.decode()
    sent.clear()
    weekly.generate({**config.DEFAULTS, "offline": True, "quota": False, "mask_projects": True,
                     "ai_summary": {"enabled": True, "include_project_names": True}}, UTC)
    print("MASKED payload has PROJNAME:", "PROJNAME" in sent[0][0].data.decode())


def test_cross_origin_simple_post_spends(monkeypatch, tmp_path):
    write_logs(tmp_path, datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    sent = []
    capture(monkeypatch, sent)
    c = TestClient(create_app({**config.DEFAULTS, "offline": True, "quota": False,
                               "timezone": "utc", "ai_summary": {"enabled": True}}))
    r = c.post("/api/weekly-summary", headers={"Origin": "http://evil.example",
                                               "Content-Type": "text/plain"}, content="x")
    print("XORIGIN", r.status_code, r.json()["status"], "calls", len(sent),
          "ACAO", r.headers.get("access-control-allow-origin"))
    assert len(sent) == 0  # expected: cross-origin request refused before spending
