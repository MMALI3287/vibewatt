"""FastAPI API and package-relative React dashboard serving."""

from __future__ import annotations

import logging
import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from .. import config as configmod
from .. import pricing
from ..cli import build_report, report_zone, sync_store
from .routes import router

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def create_app(
    cfg: dict | None = None, *, extra_hosts: set[str] | None = None
) -> FastAPI:
    """`extra_hosts` adds a non-loopback bind name to the Host allowlist."""
    from .security import LocalOnly

    cfg = cfg or configmod.load()
    tz = report_zone(cfg)

    lock = threading.Lock()
    stop = threading.Event()

    cache: dict = {}

    def report(**filters):
        """A cached build_report(); an entry lives until the store changes."""
        from .. import store

        with store.connect() as conn:
            gen = store.generation(conn)
        # Today is part of the key: streaks and month-to-date move at midnight.
        from datetime import datetime

        key = (
            str(datetime.now(tz).date()),
            *sorted((k, str(v)) for k, v in filters.items()),
        )
        hit = cache.get(key)
        if hit is None or hit[0] != gen:
            if len(cache) > 64:
                cache.clear()
            hit = (gen, build_report(cfg, tz, with_quota=False, **filters))
            cache[key] = hit
        return hit[1]

    def sync_once() -> None:
        from .. import quota, store

        with lock:
            sync_store(cfg, tz)
            app.state.synced = True
            if cfg.get("quota", True):
                with store.connect() as conn:
                    # The only place the server may call the endpoint, throttled.
                    quota.refresh(conn, cfg, allow_fetch=True)
        # Warm the Overview's unfiltered report so the first view after a sync
        # does not pay for building it.
        report(
            source="all",
            date_from=None,
            date_to=None,
            project=None,
            model=None,
            parts=None,
        )

    def ensure_synced() -> None:
        # Reports read the store only. This covers the first request of a
        # server started without its lifespan (tests), never later ones.
        if not app.state.synced:
            sync_once()

    def loop(interval: float) -> None:
        while not stop.wait(interval):
            try:
                sync_once()
            except Exception:  # a bad sync must not kill the loop
                logging.getLogger("vibewatt").exception("background sync failed")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        pricing.refresh(offline=cfg.get("offline", False))
        sync_once()
        interval = float(cfg.get("sync_interval_seconds", 60))
        worker = None
        if interval > 0:
            worker = threading.Thread(target=loop, args=(interval,), daemon=True)
            worker.start()
        yield
        stop.set()
        if worker:
            worker.join(timeout=5)

    app = FastAPI(title="vibewatt", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.tz = tz
    app.state.synced = False
    app.state.sync_lock = lock
    app.state.ensure_synced = ensure_synced
    app.state.report = report

    app.include_router(router)

    # Windows registry MIME entries can otherwise prevent module scripts loading.
    for ext, mime in {
        ".js": "text/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
        ".woff2": "font/woff2",
    }.items():
        mimetypes.add_type(mime, ext)

    @app.get("/api/{path:path}", include_in_schema=False)
    @app.get("/api", include_in_schema=False)
    def unknown_api(path: str = "") -> None:
        raise HTTPException(404, "Not found")

    @app.middleware("http")
    async def frontend_headers(request: Request, call_next):
        path = request.url.path
        if "\\" in path or ".." in path.split("/"):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Not found"}, status_code=404)
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/assets", include_in_schema=False)
    def asset_root() -> None:
        raise HTTPException(404, "Not found")

    if (STATIC_DIR / "assets").is_dir():
        app.mount(
            "/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets"
        )

    if (STATIC_DIR / "index.html").is_file():
        app.frontend("/", directory=STATIC_DIR, fallback="index.html")
    else:

        @app.get("/{path:path}", include_in_schema=False)
        def missing_dashboard(path: str) -> None:
            if path.split("/", 1)[0] == "assets":
                raise HTTPException(404, "Not found")
            raise HTTPException(
                503, "Dashboard build missing. Run npm ci and npm run build in web/."
            )

    # Keep Host validation outermost, including static files and malformed paths.
    app.add_middleware(LocalOnly, extra_hosts=extra_hosts)
    return app
