"""d8 probe: PLAN 7.1 Verify -- a known session's total equals the sum of its turns.
No suite test asserts this relation; this checks the behaviour itself."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vibewatt import config as configmod
from vibewatt.api import create_app


def test_session_total_equals_sum_of_turns(logs):
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    client = TestClient(create_app(cfg))
    assert client.post("/api/sync").status_code == 200
    rows = client.get("/api/sessions").json()
    assert rows
    for row in rows:
        detail = client.get(f"/api/sessions/{row['id']}").json()
        turns = detail["turns"]
        tokens = sum(t["input"] + t["cache_5m"] + t["cache_1h"] + t["cache_read"] + t["output"]
                     for t in turns)
        cost = sum(t["cost"] or 0 for t in turns)
        assert row["tokens"] == tokens, (row["id"], row["tokens"], tokens)
        assert row["cost"] == pytest.approx(cost), (row["id"], row["cost"], cost)
        assert detail["tokens"] == row["tokens"]
