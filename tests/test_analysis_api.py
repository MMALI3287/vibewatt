from __future__ import annotations

from datetime import UTC
from pathlib import Path

from fastapi.testclient import TestClient

from vibewatt import store
from vibewatt.aggregate import cost_of
from vibewatt.api import create_app


def client_with_reads():
    with store.connect() as conn:
        store.sync_files(
            conn,
            [
                (
                    "claude-code",
                    Path(__file__).parent / "fixtures" / "repeated_reads.jsonl",
                )
            ],
            UTC,
            cost_of,
        )
    app = create_app({"offline": True, "quota": False, "timezone": "utc"})
    return TestClient(app)


def test_findings_refresh_filter_dismiss_restore_and_metric_stability():
    client = client_with_reads()
    response = client.get("/api/findings")
    assert response.status_code == 200
    findings = response.json()["findings"]
    repeated = next(f for f in findings if f["rule"] == "repeated_reads")
    assert repeated["metrics"]["read_calls"] == 3
    refresh = client.post("/api/analysis").json()["findings"]
    assert [f["id"] for f in refresh] == [f["id"] for f in findings]
    assert client.get("/api/findings?kind=context").json()["findings"] == []
    assert client.get("/api/findings?severity=urgent").json()["findings"] == []
    assert client.get("/api/findings?source=web").json()["findings"] == []
    assert client.get("/api/findings?from=2026-09-02").json()["findings"] == []
    assert client.get("/api/findings?project=unmatched").json()["findings"] == []
    assert client.get("/api/findings?model=unmatched").json()["findings"] == []
    identifier = repeated["id"]
    assert (
        client.post(
            f"/api/findings/{identifier}/dismiss", json={"dismissed": True}
        ).status_code
        == 200
    )
    assert not client.get("/api/findings?metric=tokens").json()["findings"]
    included = client.get("/api/findings?include_dismissed=true").json()["findings"]
    assert included[0]["dismissed"]
    assert (
        client.post(
            f"/api/findings/{identifier}/dismiss", json={"dismissed": False}
        ).status_code
        == 200
    )
    assert client.get("/api/findings").json()["findings"][0]["id"] == identifier


def test_analysis_invalid_filters_and_missing_finding():
    client = client_with_reads()
    assert client.get("/api/findings?kind=bad").status_code == 422
    assert client.get("/api/findings?severity=bad").status_code == 422
    assert client.get("/api/findings?from=not-a-date").status_code == 400
    assert client.get("/api/findings?from=2026-09-02&to=2026-09-01").status_code == 400
    assert (
        client.post(
            "/api/findings/missing/dismiss", json={"dismissed": True}
        ).status_code
        == 404
    )


def test_empty_analysis_has_coverage_and_makes_no_network_calls(monkeypatch):
    import urllib.request

    def reject_network(*args, **kwargs):
        raise AssertionError("analysis must not fetch network data")

    monkeypatch.setattr(urllib.request, "urlopen", reject_network)
    client = TestClient(create_app({"offline": True, "quota": False}))
    body = client.get("/api/findings").json()
    assert body["findings"] == []
    # Nothing to evaluate is "no activity", not a false "not enough history" (A-043).
    assert (
        "Anomaly detection: no local activity in this range." in body["anomaly_notes"]
    )
