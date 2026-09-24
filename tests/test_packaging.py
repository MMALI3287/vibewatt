from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vibewatt.api import app as appmod


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    static = tmp_path / "installed package Ã¦â€”Â¥Ã¦Å“Â¬Ã¨ÂªÅ¾" / "vibewatt" / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text('<div id="root">fixture React shell</div>')
    (static / "assets" / "app.js").write_text("console.log('fixture');")
    monkeypatch.setattr(appmod, "STATIC_DIR", static)
    monkeypatch.chdir(tmp_path)
    return TestClient(appmod.create_app({"offline": True, "quota": False}))


@pytest.mark.parametrize("path", ["/", "/index.html", "/sessions/abc", "/wrapped"])
def test_installed_spa_paths(dashboard, path):
    response = dashboard.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "fixture React shell" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_assets_and_api_are_not_swallowed(dashboard):
    assert dashboard.get("/assets/app.js").text == "console.log('fixture');"
    for path in ["/assets/missing.js", "/api/absent", "/api", "/assets"]:
        assert dashboard.get(path, headers={"Accept": "text/html"}).status_code == 404
    assert dashboard.get("/api/health").status_code == 200
    for path in ["/%2e%2e/private", "/..%5cprivate"]:
        assert dashboard.get(path, headers={"Accept": "text/html"}).status_code == 404


def test_source_checkout_without_build(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "STATIC_DIR", tmp_path / "missing")
    client = TestClient(appmod.create_app({"offline": True, "quota": False}))
    assert client.get("/").status_code == 503
    assert client.get("/api/health").status_code == 200


def test_windows_registry_cannot_break_asset_mime(tmp_path, monkeypatch):
    import mimetypes

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text('<div id="root"></div>')
    expected = {
        "js": "text/javascript",
        "css": "text/css",
        "svg": "image/svg+xml",
        "woff2": "font/woff2",
    }
    for ext in expected:
        (static / "assets" / f"app.{ext}").write_bytes(b"fixture")
        mimetypes.add_type("text/plain", f".{ext}")
    monkeypatch.setattr(appmod, "STATIC_DIR", static)
    client = TestClient(appmod.create_app({"offline": True, "quota": False}))
    for ext, mime in expected.items():
        assert (
            client.get(f"/assets/app.{ext}").headers["content-type"].split(";")[0]
            == mime
        )
