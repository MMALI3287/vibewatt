"""d2 audit probes: shared filters on wrapped/findings/blocks."""

from __future__ import annotations

from fastapi.testclient import TestClient
from test_d2_api import JST, tree  # noqa: F401

from vibewatt.api import create_app


def test_filters_on_wrapped_findings(tree, capsys):  # noqa: F811
    app = create_app({"offline": True, "quota": False, "timezone": "utc"})
    app.state.tz = JST
    c = TestClient(app)
    c.post("/api/sync")
    out = {}
    for params in ({}, {"source": "cowork"}, {"project": "alpha"}, {"model": "claude-sonnet-5"},
                   {"from": "2026-09-11", "to": "2026-09-11"}):
        w = c.get("/api/wrapped", params={"year": 2026, **params}).json()
        s = c.get("/api/summary", params=params).json()["total"]
        f = c.get("/api/findings", params=params)
        out[str(params)] = (w["local_summary"]["total"]["responses"], s["responses"],
                            round(w["stored_cost_usd"], 6), f.status_code)
    for k, v in out.items():
        print(k, "wrapped.local_summary.responses, summary.responses, wrapped.stored_cost, findings status =", v)
    mism = {k: v for k, v in out.items() if v[0] != v[1]}
    assert not mism, mism
