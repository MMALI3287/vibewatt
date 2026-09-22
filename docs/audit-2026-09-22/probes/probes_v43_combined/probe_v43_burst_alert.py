"""v43: end-to-end burn alert under the app's own per-request quota sampling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc


def _run(monkeypatch, order):
    from fastapi.testclient import TestClient

    from ccburn import config, quota, store
    from ccburn.api import create_app

    now = datetime.now(UTC)
    reset = now + timedelta(hours=4, minutes=30)
    state = {"load": 0, "tick": 0}

    def fake_fetch(token=None, timeout=10.0):
        state["tick"] += 1
        # 6 loads 5 min apart ending ~now; 20% -> 30%: 2%/5min => ~128% at reset
        at = now - timedelta(minutes=5 * (5 - state["load"])) + timedelta(milliseconds=state["tick"])
        util = 20.0 + 2 * state["load"]
        return quota.Quota([quota.Window("5-hour", util, reset)], "endpoint", at)

    monkeypatch.setattr(quota, "read_token", lambda: "fake")
    monkeypatch.setattr(quota, "fetch", fake_fetch)
    c = TestClient(create_app({**config.DEFAULTS, "offline": True, "timezone": "utc"}))
    new = 0
    burn = []
    for load in range(6):
        state["load"] = load
        for path in order:
            r = c.get(path)
            assert r.status_code == 200
            if path == "/api/alerts":
                new += r.json()["new_count"]
                burn += [a for a in r.json()["alerts"] if a["kind"] == "burn"]
    with store.connect() as conn:
        n = conn.execute("SELECT count(*) FROM quota_samples").fetchone()[0]
    return new, burn, n


@pytest.mark.parametrize("order", [
    ("/api/alerts", "/api/summary", "/api/quota", "/api/blocks"),
    ("/api/summary", "/api/quota", "/api/blocks", "/api/alerts"),
    ("/api/summary", "/api/alerts", "/api/quota", "/api/blocks"),
])
def test_burn_alert_end_to_end(monkeypatch, order):
    new, burn, n = _run(monkeypatch, order)
    print("ORDER", order[0], "->", order[-1], "samples", n, "new_count", new, "burn", len(burn))
    assert new == 1


def test_control_one_sample_per_load(monkeypatch):
    new, burn, n = _run(monkeypatch, ("/api/quota", "/api/alerts"))
    print("CONTROL samples", n, "new_count", new, burn[:1])
    assert new == 1
