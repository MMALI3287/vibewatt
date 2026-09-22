"""d2 audit probes: API consistency, validation, harvest, quota call counts."""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ccburn import cli as climod
from ccburn import config as configmod
from ccburn import quota as quotamod
from ccburn.api import create_app

JST = timezone(timedelta(hours=9))


def _line(rid, ts, sess, cwd, mid, model, inp, out, c5=0, c1=0, cr=0, sidechain=False):
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
             "cache_creation_input_tokens": c5 + c1,
             "cache_creation": {"ephemeral_5m_input_tokens": c5, "ephemeral_1h_input_tokens": c1}}
    return json.dumps({"type": "assistant", "requestId": rid, "timestamp": ts, "sessionId": sess,
                       "cwd": cwd, "isSidechain": sidechain,
                       "message": {"id": mid, "model": model, "usage": usage}})


@pytest.fixture
def tree(tmp_path):
    """Two projects, two models, three JST days, one unpriced model, one cowork session."""
    base = tmp_path / "claude" / "projects"
    a = base / "alpha" / "sa.jsonl"
    b = base / "beta" / "sb.jsonl"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a_lines = [
        _line("r1", "2026-09-10T01:00:00Z", "sa", "/w/alpha", "m1", "claude-opus-5", 1000, 200, c5=500, c1=300, cr=50),
        _line("r1", "2026-09-10T01:00:00Z", "sa", "/w/alpha", "m1", "claude-opus-5", 1000, 200, c5=500, c1=300, cr=50),
        # 20:00Z is next JST day
        _line("r2", "2026-09-10T20:00:00Z", "sa", "/w/alpha", "m2", "claude-sonnet-5", 400, 100, cr=900),
        _line("r3", "2026-09-12T03:00:00Z", "sa", "/w/alpha", "m3", "claude-opus-5", 10, 5, sidechain=True),
        json.dumps({"type": "last-prompt", "sessionId": "sa", "timestamp": "2026-09-10T01:00:00Z",
                    "lastPrompt": "alpha work"}),
    ]
    b_lines = [
        _line("r4", "2026-09-11T05:00:00Z", "sb", "/w/beta", "m4", "claude-opus-5", 70, 30),
        _line("r5", "2026-09-11T06:00:00Z", "sb", "/w/beta", "m5", "claude-unknown-9", 80, 40),
    ]
    a.write_text("\n".join(a_lines) + "\n", encoding="utf-8")
    b.write_text("\n".join(b_lines) + "\n", encoding="utf-8")
    cw = tmp_path / "appdata" / "Claude" / "local-agent-mode-sessions" / "acct" / "space" / "id" / "audit.jsonl"
    cw.parent.mkdir(parents=True)
    cw.write_text(json.dumps({"type": "assistant", "requestId": "r9", "_audit_timestamp": "2026-09-11T02:00:00Z",
                              "session_id": "c1", "message": {"id": "m9", "model": "claude-opus-5",
                              "usage": {"input_tokens": 30, "cache_creation_input_tokens": 0,
                                        "cache_read_input_tokens": 0, "output_tokens": 5}}}) + "\n",
                  encoding="utf-8")
    return tmp_path


def _client(**extra):
    cfg = configmod.load()
    cfg.update({"offline": True, "quota": False, "timezone": "utc"})
    cfg.update(extra)
    app = create_app(cfg)
    app.state.tz = JST
    return TestClient(app), cfg


def _tok(b):
    return b["input"] + b["cache_write_5m"] + b["cache_write_1h"] + b["cache_read"] + b["output"]


FILTER_SETS = [
    {},
    {"source": "claude-code"},
    {"source": "cowork"},
    {"project": "alpha"},
    {"model": "claude-opus-5"},
    {"from": "2026-09-11", "to": "2026-09-11"},
    {"from": "2026-09-11", "project": "alpha"},
]


def test_cross_endpoint_consistency(tree, capsys):
    client, _ = _client()
    assert client.post("/api/sync").status_code == 200
    problems = []
    for f in FILTER_SETS:
        s = client.get("/api/summary", params=f).json()
        tot = s["total"]
        row = {"filters": f, "summary": (tot["responses"], round(tot["cost_usd"], 6), _tok(tot))}
        daily = client.get("/api/daily", params=f).json()
        row["daily"] = (sum(b["responses"] for b in daily.values()),
                        round(sum(b["cost_usd"] for b in daily.values()), 6),
                        sum(_tok(b) for b in daily.values()))
        for dim in ("model", "project", "source", "surface"):
            bd = client.get(f"/api/breakdown/{dim}", params=f).json()
            row[dim] = (sum(b["responses"] for b in bd.values()),
                        round(sum(b["cost_usd"] for b in bd.values()), 6),
                        sum(_tok(b) for b in bd.values()))
        sess = [r for r in client.get("/api/sessions", params={**f, "limit": 500}).json() if not r["harvested"]]
        row["sessions"] = (None, round(sum(r["cost"] for r in sess), 6), sum(r["tokens"] for r in sess))
        row["session_count"] = (len(sess), s["sessions"])
        exp = json.loads(client.get("/api/export", params={**f, "format": "json"}).text)["total"]
        row["export_json"] = (exp["responses"], round(exp["cost_usd"], 6), _tok(exp))
        csv_text = client.get("/api/export", params={**f, "format": "csv"}).text
        row["csv_head"] = csv_text.splitlines()[0]
        print(row)
        for k in ("daily", "model", "project", "source", "surface", "export_json"):
            if row[k] != row["summary"]:
                problems.append((f, k, row[k], row["summary"]))
        if row["sessions"][1:] != row["summary"][1:]:
            problems.append((f, "sessions", row["sessions"], row["summary"]))
        if row["session_count"][0] != row["session_count"][1]:
            problems.append((f, "session_count", row["session_count"]))
    print("PROBLEMS", *problems, sep="\n  ")
    assert not problems


def test_project_alias_filter_roundtrip(tree, capsys):
    client, _ = _client(project_aliases={"alpha": "Alpha Renamed"})
    client.post("/api/sync")
    keys = sorted(client.get("/api/breakdown/project").json())
    facets = client.get("/api/session-facets").json()["projects"]
    by_alias = client.get("/api/summary", params={"project": "Alpha Renamed"}).json()["total"]["responses"]
    by_raw = client.get("/api/summary", params={"project": "alpha"}).json()
    sess_proj = sorted({r["project"] for r in client.get("/api/sessions").json()})
    print("breakdown keys", keys, "| facets", facets, "| sessions projects", sess_proj)
    print("summary?project=<alias key> responses", by_alias,
          "| summary?project=alpha by_project keys", list(by_raw["by_project"]))
    assert by_alias > 0, "project key shown by /api/breakdown/project cannot be used as a filter"


def test_mask_projects_filter_roundtrip(tree, capsys):
    client, _ = _client(mask_projects=True)
    keys = sorted(client.get("/api/breakdown/project").json())
    hits = {k: client.get("/api/summary", params={"project": k}).json()["total"]["responses"] for k in keys}
    facets = client.get("/api/session-facets").json()["projects"]
    print("masked keys -> responses when used as filter", hits, "| facets (raw)", facets)
    assert all(v > 0 for v in hits.values())


VALIDATION = [
    ("get", "/api/summary", {"from": "2026-13-01"}),
    ("get", "/api/summary", {"from": "2026-09-20", "to": "2026-09-10"}),
    ("get", "/api/daily", {"from": "2026-09-20", "to": "2026-09-10"}),
    ("get", "/api/breakdown/model", {"from": "2026-09-20", "to": "2026-09-10"}),
    ("get", "/api/sessions", {"from": "2026-09-20", "to": "2026-09-10"}),
    ("get", "/api/export", {"from": "2026-09-20", "to": "2026-09-10"}),
    ("get", "/api/wrapped", {"from": "2026-09-20", "to": "2026-09-10"}),
    ("get", "/api/findings", {"from": "2026-09-20", "to": "2026-09-10"}),
    ("get", "/api/breakdown/bogus", {}),
    ("get", "/api/sessions", {"limit": 0}),
    ("get", "/api/sessions", {"limit": -5}),
    ("get", "/api/sessions", {"limit": 10**9}),
    ("get", "/api/sessions", {"cursor": "garbage"}),
    ("get", "/api/sessions", {"cursor": "2026-09-10T01:00:00+00:00|"}),
    ("get", "/api/sessions", {"cursor": "\x00\x01"}),
    ("get", "/api/wrapped", {"year": 0}),
    ("get", "/api/wrapped", {"year": "abc"}),
    ("get", "/api/findings", {"kind": "bogus"}),
    ("get", "/api/export", {"format": "xml"}),
    ("get", "/api/summary", {"source": "bogus"}),
    ("get", "/api/summary", {"metric": "bogus"}),
    ("get", "/api/summary", {"from": "20260910"}),
    ("get", "/api/concierge", {}),
]


def test_validation_statuses(tree, capsys):
    client, _ = _client()
    client.post("/api/sync")
    table = []
    for method, path, params in VALIDATION:
        r = getattr(client, method)(path, params=params)
        table.append((r.status_code, path, params, r.text[:90]))
    for row in table:
        print(row)
    assert not [t for t in table if t[0] >= 500]


def test_harvest_malformed_bodies(tmp_path, capsys):
    client = TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}),
                        raise_server_exceptions=False)
    cases = {
        "invalid_json": dict(content=b"{not json", headers={"content-type": "application/json"}),
        "object_wrong_shape": dict(json={"foo": 1}),
        "list_of_ints": dict(json=[1, 2, 3]),
        "meta_not_dict": dict(json=[{"id": "x1", "external_metadata": "oops"}]),
        "tokens_not_int": dict(json=[{"id": "x2", "external_metadata": {"usage": {"input_tokens": "abc"}}}]),
    }
    out = {}
    for name, kw in cases.items():
        r = client.post("/api/harvest", **kw)
        out[name] = (r.status_code, r.text[:80])
    print(out)
    assert all(code < 500 for code, _ in out.values())


def _cloud(sid, cost, title="t"):
    return {"id": sid, "title": title, "origin": "web_claude_ai",
            "created_at": "2026-09-15T00:00:00Z", "updated_at": "2026-09-15T00:10:00Z",
            "session_context": {"model": "claude-opus-5"},
            "external_metadata": {"usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                                            "cost_usd": cost}}}


def test_harvest_cost_unchanged_and_idempotent(tmp_path, capsys):
    client = TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}))
    payload = {"data": [_cloud("session_A", 0.42)]}
    r1 = client.post("/api/harvest", json=payload).json()
    r2 = client.post("/api/harvest", json=payload).json()
    rows = [r for r in client.get("/api/sessions").json() if r["harvested"]]
    health = client.get("/api/health").json()
    print("harvest", r1, r2, "rows", [(r["id"], r["cost"], r["tokens"]) for r in rows],
          "health.cloud", health["cloud"], "last_harvest", health["last_harvest"])
    assert r1 == r2 == {"written": 1, "skipped": 0}
    assert len(rows) == 1 and rows[0]["cost"] == 0.42  # opus-5 recompute would be $30
    assert health["cloud"]["n"] == 1 and health["cloud"]["cost"] == 0.42


def test_harvest_without_id_is_not_idempotent_and_breaks_listing(tmp_path, capsys):
    client = TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}),
                        raise_server_exceptions=False)
    entry = _cloud(None, 1.0, title=None)
    client.post("/api/harvest", json=[entry])
    client.post("/api/harvest", json=[entry])
    health = client.get("/api/health").json()
    listing = client.get("/api/sessions")
    print("health.cloud after 2 identical id-less harvests", health["cloud"],
          "| /api/sessions status", listing.status_code, listing.text[:80])
    assert health["cloud"]["n"] <= 1
    assert listing.status_code == 200


def test_harvest_missing_cost_usd_priced_zero(tmp_path, capsys):
    client = TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}))
    entry = _cloud("session_nocost", 0)
    del entry["external_metadata"]["usage"]["cost_usd"]
    client.post("/api/harvest", json=[entry])
    row = client.get("/api/sessions/session_nocost").json()
    print("cloud session with 2M tokens and no cost_usd ->", row["cost"], "unpriced_turns", row["unpriced_turns"])
    assert not (row["cost"] == 0 and row["unpriced_turns"] == 0)


def test_sync_does_not_stream(tree, capsys):
    client, _ = _client()
    with client.stream("POST", "/api/sync") as r:
        chunks = list(r.iter_raw())
        ctype = r.headers.get("content-type")
    print("content-type", ctype, "chunks", len(chunks), chunks[0][:120])
    assert ctype.startswith("text/event-stream") or ctype.startswith("application/x-ndjson")


def test_health_has_last_sync_and_coverage(tree, capsys):
    client, _ = _client()
    client.post("/api/sync")
    body = client.get("/api/health").json()
    print("health keys", sorted(body))
    assert "last_sync" in body or any("sync" in k for k in body)
    assert any("gap" in k or "coverage" in k for k in body)


def test_quota_has_recent_samples(tree, monkeypatch, capsys):
    from datetime import datetime

    now = datetime.now(timezone.utc)
    fake = quotamod.Quota(source="test", fetched_at=now,
                          windows=[quotamod.Window(label="5h", utilization=42.0, resets_at=now + timedelta(hours=1))]) \
        if hasattr(quotamod, "Quota") else None
    if fake is None:
        pytest.skip("no Quota dataclass")
    monkeypatch.setattr(quotamod, "read", lambda cfg: (fake, None))
    client, _ = _client()
    body = client.get("/api/quota").json()
    print("quota body keys", sorted(body))
    assert any("sample" in k for k in body)


def test_overview_quota_read_count(tree, monkeypatch, capsys):
    calls = {"quota": 0, "build": 0}
    real_read = quotamod.read
    real_build = climod.build_report

    def counting_read(cfg):
        calls["quota"] += 1
        return real_read(cfg)

    def counting_build(*a, **k):
        calls["build"] += 1
        return real_build(*a, **k)

    monkeypatch.setattr(quotamod, "read", counting_read)
    monkeypatch.setattr(climod, "build_report", counting_build)
    client, _ = _client()
    # Default Overview load: summary (shared key with FilterBar/unfiltered), quota (Hero),
    # blocks (UsageCharts), session-facets (FilterBar), health (Footer), alerts+status (Phase6Panels).
    for path in ("/api/summary", "/api/quota", "/api/blocks", "/api/session-facets",
                 "/api/health", "/api/alerts", "/api/status"):
        assert client.get(path).status_code == 200
    default = dict(calls)
    calls.update(quota=0, build=0)
    # Filtered Overview load: filtered summary + unfiltered summary + filtered blocks + quota.
    for path, params in (("/api/summary", {"project": "alpha"}), ("/api/summary", {}),
                         ("/api/blocks", {"project": "alpha"}), ("/api/quota", {"project": "alpha"})):
        client.get(path, params=params)
    print("default Overview load:", default, "| filtered Overview load:", calls)
    assert default["quota"] <= 1
