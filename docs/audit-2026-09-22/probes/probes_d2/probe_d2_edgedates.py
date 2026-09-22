"""d2 audit probe: extreme but valid ISO dates must not 500."""

from __future__ import annotations

from fastapi.testclient import TestClient
from test_d2_api import JST, tree  # noqa: F401

from vibewatt.api import create_app


def test_extreme_dates(tree, capsys):  # noqa: F811
    app = create_app({"offline": True, "quota": False, "timezone": "utc"})
    app.state.tz = JST
    c = TestClient(app, raise_server_exceptions=False)
    c.post("/api/sync")
    out = []
    for path in ("/api/summary", "/api/daily", "/api/sessions", "/api/findings", "/api/export", "/api/blocks"):
        for params in ({"from": "0001-01-01"}, {"to": "9999-12-31"}):
            r = c.get(path, params=params)
            out.append((r.status_code, path, params))
    for row in out:
        print(row)
    assert not [o for o in out if o[0] >= 500]
