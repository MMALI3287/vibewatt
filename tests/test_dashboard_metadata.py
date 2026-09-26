from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from vibewatt import config, store
from vibewatt.api import create_app, routes


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 16, 18, tzinfo=UTC).astimezone(tz)


def client(monkeypatch, **options):
    monkeypatch.setattr(routes, "datetime", Clock)
    cfg = config.load()
    cfg.update(offline=True, quota=False, timezone="Asia/Tokyo")
    cfg.update(options)
    return TestClient(create_app(cfg))


def test_report_context_uses_shifted_server_day_without_sync(monkeypatch):
    c = client(monkeypatch, day_start_hour=6)
    c.app.state.ensure_synced = lambda: pytest.fail("context must not sync logs")
    response = c.get("/api/report-context")
    assert response.status_code == 200
    assert response.json() == {
        "today": "2026-09-16",
        "timezone": "Asia/Tokyo",
        "day_start_hour": 6,
        "as_of": None,
    }


def test_plan_comparison_uses_current_month_and_keeps_other_filters(logs, monkeypatch):
    c = client(monkeypatch, plan_usd_per_month=20)
    total = c.get("/api/summary").json()
    comparison = total["plan_comparison"]
    assert comparison["period_start"] == "2026-09-01"
    assert comparison["period_end"] == "2026-09-17"
    assert comparison["multiple"] == pytest.approx(total["total"]["cost_usd"] / 20)
    historical = c.get("/api/summary?from=2025-01-01&to=2025-01-31").json()
    assert historical["total"]["responses"] == 0
    assert historical["plan_comparison"] == comparison
    empty = c.get("/api/summary?project=nonexistent").json()
    assert empty["plan_comparison"]["multiple"] == 0
    assert total["provenance"]["usage"] == "computed_local"
    assert total["provenance"]["cost"] == "estimate"
    assert total["provenance"]["as_of"] == c.get("/api/health").json()["last_sync"]


@pytest.mark.parametrize("price", [None, 0, -1, "20", True, float("inf")])
def test_invalid_or_unconfigured_plan_has_no_comparison(logs, monkeypatch, price):
    assert (
        client(monkeypatch, plan_usd_per_month=price)
        .get("/api/summary")
        .json()["plan_comparison"]
        is None
    )


def test_unknown_cost_suppresses_plan_multiple(logs, monkeypatch):
    c = client(monkeypatch, plan_usd_per_month=20)
    c.post("/api/sync")
    with store.connect() as conn:
        conn.execute("UPDATE turns SET cost = NULL")
        store.rebuild_rollup(conn)
    comparison = c.get("/api/summary").json()["plan_comparison"]
    assert comparison["multiple"] is None
    assert comparison["unpriced"] > 0


def test_session_provenance_distinguishes_cloud_and_local(logs, monkeypatch):
    c = client(monkeypatch)
    c.post("/api/sync")
    c.post(
        "/api/harvest",
        json=[
            {
                "id": "cloud",
                "created_at": "2026-09-15T00:00:00Z",
                "updated_at": "2026-09-15T01:00:00Z",
                "origin": "web_claude_ai",
                "environment_kind": "anthropic_cloud",
                "external_metadata": {"usage": {"input_tokens": 100, "cost_usd": 4}},
            }
        ],
    )
    rows = c.get("/api/sessions").json()
    cloud = next(r for r in rows if r["id"] == "cloud")
    assert cloud["provenance"] == {
        "usage": "cloud_reported",
        "cost": "cloud_reported",
        "scope": "harvested_session",
        "as_of": "2026-09-15T01:00:00Z",
    }
    assert c.get("/api/sessions/cloud").json()["provenance"] == cloud["provenance"]
    assert (
        next(r for r in rows if not r["harvested"])["provenance"]["cost"] == "estimate"
    )
    wrapped = c.get("/api/wrapped?year=2026").json()
    assert wrapped["provenance_by_source"]["local"]["usage"] == "computed_local"
    assert wrapped["provenance_by_source"]["cloud"]["usage"] == "cloud_reported"
