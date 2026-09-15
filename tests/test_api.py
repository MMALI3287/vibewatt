from __future__ import annotations

from fastapi.testclient import TestClient

from ccburn import cli as climod
from ccburn import config as configmod
from ccburn.api import create_app


def _app():
    cfg = configmod.load()
    cfg["offline"] = True  # never fetch pricing or quota over the network in tests
    cfg["quota"] = False
    return create_app(cfg), cfg


def _client() -> TestClient:
    app, _ = _app()
    return TestClient(app)


def test_summary_matches_cli_json_totals(logs):
    client = _client()
    _, cfg = _app()
    tz = climod.resolve_tz(cfg.get("timezone"))
    report, *_ = climod.build_report(cfg, tz)
    expected = climod.serialize(report)

    resp = client.get("/api/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"]["responses"] == expected["total"]["responses"]
    assert body["total"]["cost_usd"] == expected["total"]["cost_usd"]
    assert body["total"]["input"] == expected["total"]["input"]
    assert body["sessions"] == expected["sessions"]


def test_daily_breakdown_matches_summary_by_day(logs):
    client = _client()
    summary = client.get("/api/summary").json()
    daily = client.get("/api/daily").json()
    assert daily == summary["by_day"]


def test_hourly_breakdown_returns_hour_keys(logs):
    client = _client()
    resp = client.get("/api/hourly")
    assert resp.status_code == 200
    for key in resp.json():
        assert key.isdigit()


def test_breakdown_by_model(logs):
    client = _client()
    summary = client.get("/api/summary").json()
    breakdown = client.get("/api/breakdown/model").json()
    assert breakdown == summary["by_model"]


def test_breakdown_unknown_dimension_is_404(logs):
    client = _client()
    resp = client.get("/api/breakdown/bogus")
    assert resp.status_code == 404


def test_sync_then_sessions_list_and_detail(logs):
    client = _client()

    synced = client.post("/api/sync")
    assert synced.status_code == 200
    assert synced.json()["turns"] == 3  # matches test_store's fixture expectation

    sessions = client.get("/api/sessions")
    assert sessions.status_code == 200
    rows = sessions.json()
    assert len(rows) >= 1
    assert rows[0]["harvested"] is False

    detail = client.get(f"/api/sessions/{rows[0]['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == rows[0]["id"]
    assert len(body["turns"]) >= 1


def test_sessions_list_filters_by_project(logs):
    client = _client()
    client.post("/api/sync")
    all_rows = client.get("/api/sessions").json()
    assert all_rows
    real_project = all_rows[0]["project"]

    matching = client.get("/api/sessions", params={"project": real_project}).json()
    assert matching and all(r["project"] == real_project for r in matching)

    none_matching = client.get("/api/sessions", params={"project": "no-such-project"}).json()
    assert none_matching == []


def test_sessions_list_cursor_pages_past_seen_rows(logs):
    client = _client()
    client.post("/api/sync")
    first_page = client.get("/api/sessions", params={"limit": 1}).json()
    assert len(first_page) == 1
    next_page = client.get(
        "/api/sessions", params={"limit": 40, "cursor": first_page[0]["started"]}
    ).json()
    assert first_page[0]["id"] not in {r["id"] for r in next_page}


def test_session_detail_missing_id_is_404(logs):
    client = _client()
    client.post("/api/sync")
    resp = client.get("/api/sessions/does-not-exist")
    assert resp.status_code == 404


def test_harvest_ingests_a_cloud_session(tmp_path):
    client = _client()
    payload = [{
        "id": "session_01H1",
        "title": "cloud test session",
        "origin": "web_claude_ai",
        "created_at": "2026-09-15T00:00:00Z",
        "updated_at": "2026-09-15T00:10:00Z",
        "session_context": {"model": "claude-opus-5"},
        "external_metadata": {
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_read_tokens": 0, "cache_write_tokens": 0,
                      "cost_usd": 1.23},
        },
    }]
    resp = client.post("/api/harvest", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"written": 1, "skipped": 0}

    sessions = client.get("/api/sessions").json()
    cloud_rows = [r for r in sessions if r["harvested"]]
    assert cloud_rows and cloud_rows[0]["id"] == "session_01H1"

    detail = client.get("/api/sessions/session_01H1")
    assert detail.status_code == 200
    assert detail.json()["cost"] == 1.23


def test_blocks_endpoint_returns_a_list(logs):
    client = _client()
    resp = client.get("/api/blocks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_quota_endpoint_is_none_when_signed_out(logs):
    client = _client()
    resp = client.get("/api/quota")
    assert resp.status_code == 200
    assert resp.json() is None


def test_health_endpoint_labels_itself_local_only(tmp_path):
    client = _client()
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert "local" in resp.json()["note"]


def test_export_csv_and_json(logs):
    client = _client()
    csv_resp = client.get("/api/export", params={"format": "csv"})
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")

    json_resp = client.get("/api/export", params={"format": "json"})
    assert json_resp.status_code == 200

    bad = client.get("/api/export", params={"format": "xml"})
    assert bad.status_code == 400


def test_bad_date_filter_is_400(logs):
    client = _client()
    resp = client.get("/api/summary", params={"from": "not-a-date"})
    assert resp.status_code == 400


def test_legacy_usage_route_still_works(logs):
    client = _client()
    resp = client.get("/api/usage")
    assert resp.status_code == 200
    assert "total" in resp.json()
