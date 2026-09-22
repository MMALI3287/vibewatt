"""d2 audit probe: mask_projects coverage across endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from test_d2_api import JST, tree  # noqa: F401

from vibewatt.api import create_app


def test_mask_projects_everywhere(tree, capsys):  # noqa: F811
    app = create_app({"offline": True, "quota": False, "timezone": "utc", "mask_projects": True})
    app.state.tz = JST
    c = TestClient(app)
    c.post("/api/sync")
    seen = {
        "summary.by_project": sorted(c.get("/api/summary").json()["by_project"]),
        "sessions.project": sorted({r["project"] for r in c.get("/api/sessions").json()}),
        "session-facets.projects": c.get("/api/session-facets").json()["projects"],
        "sessions/sa.turns.project": sorted({t["project"] for t in c.get("/api/sessions/sa").json()["turns"]}),
        "wrapped.top_projects": [p["name"] for p in c.get("/api/wrapped", params={"year": 2026}).json()["top_projects"]],
        "wrapped.local_summary.by_project": sorted(c.get("/api/wrapped", params={"year": 2026}).json()["local_summary"]["by_project"]),
    }
    for k, v in seen.items():
        print(f"{k:34} {v}")
    leaks = {k: v for k, v in seen.items() if any(n in ("alpha", "beta") for n in v)}
    assert not leaks, leaks
