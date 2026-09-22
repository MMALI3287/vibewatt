from __future__ import annotations

from fastapi.testclient import TestClient

from ccburn.api import create_app


def test_foreign_host_header_is_served(logs):
    app = create_app({"offline": True, "quota": False})
    c = TestClient(app)
    evil = {"Host": "evil.example:8777"}
    for method, path in [("post", "/api/sync"), ("get", "/api/sessions"),
                         ("get", "/api/export?format=json"), ("get", "/"),
                         ("get", "/api/summary")]:
        r = getattr(c, method)(path, headers=evil)
        print(method, path, "via evil host:", r.status_code, len(r.content))
    # simple (no-preflight) cross-site POST shape: text/plain body, foreign Origin
    r = c.post("/api/sync", headers={"Origin": "https://evil.example",
                                     "Content-Type": "text/plain"}, content="x")
    print("csrf-style sync:", r.status_code, r.headers.get("access-control-allow-origin"))
