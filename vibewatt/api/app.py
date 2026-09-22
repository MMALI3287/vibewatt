"""FastAPI API and package-relative React dashboard serving."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config as configmod
from .. import pricing
from ..cli import build_report, report_zone, serialize, sync_store
from .routes import _quota_out, router

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

def create_app(cfg: dict | None = None, *, extra_hosts: set[str] | None = None) -> FastAPI:
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

        key = (str(datetime.now(tz).date()), *sorted((k, str(v)) for k, v in filters.items()))
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
        report(source="all", date_from=None, date_to=None, project=None, model=None,
               parts=None)

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
    # Added last, so it runs first: nothing reaches a route from a foreign Host.
    app.add_middleware(LocalOnly, extra_hosts=extra_hosts)

    @app.get("/api/usage", include_in_schema=False)
    def legacy_usage() -> JSONResponse:
        ensure_synced()
        report, q, *_ = build_report(cfg, tz)
        payload = serialize(report)
        quota_out = _quota_out(q)
        if quota_out is not None:
            payload["quota"] = quota_out.model_dump()
        return JSONResponse(payload)

    @app.get("/api/dataset", include_in_schema=False)
    def legacy_dataset() -> JSONResponse:
        from ..ui import build_dataset

        ensure_synced()
        report, q, _, duplicates, _ = build_report(cfg, tz)
        return JSONResponse(json.loads(json.dumps(build_dataset(report, cfg, quota=q,
                                                                  duplicates=duplicates))))

    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard(path: str) -> FileResponse:
        # API mistakes and absent assets must never receive an HTML success response.
        if path.split("/", 1)[0] in {"api", "assets"}:
            raise HTTPException(404, "Not found")
        if "\\" in path or ".." in path.split("/"):
            raise HTTPException(404, "Not found")
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(
                503, "Dashboard build missing. Run npm ci and npm run build in web/."
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
