"""FastAPI app factory. Mounts every `/api/*` route from `routes.py` plus the
pre-React dashboard (`/`, `/api/dataset`, `/api/usage`) so `vibewatt serve` keeps
working until the phase 3 frontend replaces it."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .. import config as configmod
from .. import pricing
from ..cli import build_report, resolve_tz, serialize
from .routes import _quota_out, router


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or configmod.load()
    tz = resolve_tz(cfg.get("timezone"))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        pricing.refresh(offline=cfg.get("offline", False))
        yield

    app = FastAPI(title="vibewatt", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.tz = tz

    app.include_router(router)

    @app.get("/api/usage")
    def legacy_usage() -> JSONResponse:
        report, q, *_ = build_report(cfg, tz)
        payload = serialize(report)
        quota_out = _quota_out(q)
        if quota_out is not None:
            payload["quota"] = quota_out.model_dump()
        return JSONResponse(payload)

    @app.get("/api/dataset")
    def legacy_dataset() -> JSONResponse:
        from ..ui import build_dataset

        report, q, _, duplicates, _ = build_report(cfg, tz)
        return JSONResponse(json.loads(json.dumps(build_dataset(report, cfg, quota=q,
                                                                  duplicates=duplicates))))

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    def legacy_page() -> str:
        from ..ui import build_dataset, build_page

        report, q, _, duplicates, _ = build_report(cfg, tz)
        return build_page(build_dataset(report, cfg, quota=q, duplicates=duplicates))

    return app
