from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

from fastapi.testclient import TestClient

from vibewatt import config, store
from vibewatt.api import create_app


def client():
    return TestClient(
        create_app(
            {
                **config.DEFAULTS,
                "offline": True,
                "quota": False,
                "timezone": "utc",
                "plan_usd_per_month": 20,
            }
        )
    )


def test_wrapped_snapshot_and_summary_parity(logs):
    api = client()
    assert api.post("/api/sync").status_code == 200
    with store.connect() as conn:
        store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": "cloud-year",
                    "title": "Cloud review",
                    "created_at": "2026-09-16T00:00:00Z",
                    "origin": "web_claude_ai",
                    "session_context": {"model": "claude-opus-5"},
                    "external_metadata": {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cost_usd": 12.345678,
                        }
                    },
                }
            ],
        )
    response = api.get("/api/wrapped?year=2026")
    assert response.status_code == 200
    data = response.json()
    summary = api.get("/api/summary?from=2026-01-01&to=2026-12-31").json()
    assert data["local_summary"]["total"] == summary["total"]
    assert data["harvested_cost_usd"] == 12.345678
    # Dynamic block status is irrelevant to a historical year snapshot.
    data["local_summary"].pop("active_block", None)
    expected = json.loads(
        (Path(__file__).parent / "fixtures/wrapped_2026.json").read_text()
    )
    assert data == expected


def test_wrapped_empty_invalid_and_filters(logs):
    api = client()
    api.post("/api/sync")
    empty = api.get("/api/wrapped?year=2000").json()
    assert empty["stored_sessions"] == empty["longest_streak"] == 0
    assert empty["biggest_session"] is None
    assert api.get("/api/wrapped?year=10000").status_code == 422
    filtered = api.get("/api/wrapped?year=2026&source=cowork").json()
    summary = api.get("/api/summary?from=2026-01-01&to=2026-12-31&source=cowork").json()
    assert filtered["local_summary"]["total"] == summary["total"]


def test_wrapped_cloud_overlap_and_timezone(logs):
    from datetime import timedelta

    from vibewatt.analysis.wrapped import build

    api = client()
    api.post("/api/sync")
    with store.connect() as conn:
        local_id = conn.execute("SELECT session FROM turns LIMIT 1").fetchone()[0]
        store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": sid,
                    "created_at": "2025-12-31T20:00:00Z",
                    "origin": "web_claude_ai",
                    "external_metadata": {
                        "usage": {"input_tokens": 10, "cost_usd": cost}
                    },
                }
                for sid, cost in [(local_id, 999), ("boundary", 7)]
            ],
        )
        result = build(
            conn,
            {**config.DEFAULTS, "quota": False},
            timezone(timedelta(hours=9)),
            2026,
        )
    assert result["harvested_cost_usd"] == 7
    assert any(m["month"] == "2026-01" for m in result["model_months"])


def test_phase6_routes_do_not_require_network(monkeypatch):
    from vibewatt import service_status

    def forbidden():
        raise AssertionError("offline status must not fetch")

    monkeypatch.setattr(service_status, "read", forbidden)
    api = client()
    assert api.get("/api/status").json() is None
    assert api.post("/api/weekly-summary").json()["status"] == "disabled"
    assert api.get("/api/alerts").json()["new_count"] == 0
    assert api.get("/api/concierge?project=missing").status_code == 200


def test_wrapped_unknown_cost_is_explicit(logs):
    api = client()
    api.post("/api/sync")
    with store.connect() as conn:
        conn.execute("UPDATE turns SET cost = NULL, model = 'unknown-model'")
    data = api.get("/api/wrapped?year=2026").json()
    assert data["unpriced_turns"] == 3
    assert data["api_equivalent_multiple"] is None
    assert data["cache_savings_usd"] is None
    assert data["stored_tokens"] == 75


def test_wrapped_cache_savings_use_model_speed_geo(logs):
    from vibewatt.pricing import MILLION, rate_for

    api = client()
    api.post("/api/sync")
    with store.connect() as conn:
        conn.execute("UPDATE turns SET cache_read = 1000000, fast = 1, geo = 'us'")
    data = api.get("/api/wrapped?year=2026").json()
    rate = rate_for("claude-opus-5", fast=True, geo="us")
    assert data["cache_savings_usd"] == round(
        3_000_000 * (rate.input - rate.cache_read) / MILLION, 6
    )
