"""Every endpoint in PLAN.md section 5, minus /api/findings and /api/wrapped
(deferred to the phases that build the analysis engine and Wrapped)."""

from __future__ import annotations

import json as jsonlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .. import cli as climod
from .. import store
from . import schemas
from .dependencies import Filters, get_filters

router = APIRouter(prefix="/api")


def _report(request: Request, filters: Filters):
    cfg, tz = request.app.state.cfg, request.app.state.tz
    return climod.build_report(
        cfg, tz, source=filters.source, date_from=filters.date_from,
        date_to=filters.date_to, project=filters.project, model=filters.model,
    )


def _quota_out(q) -> schemas.QuotaOut | None:
    if q is None:
        return None
    return schemas.QuotaOut(
        source=q.source,
        fetched_at=q.fetched_at.isoformat(),
        windows=[
            schemas.WindowOut(
                label=w.label, utilization=w.utilization,
                resets_at=w.resets_at.isoformat() if w.resets_at else None,
            )
            for w in q.windows
        ],
    )


@router.get("/summary", response_model=schemas.SummaryOut)
def summary(request: Request, filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters)
    return climod.serialize(report)


@router.get("/daily", response_model=dict[str, schemas.BucketOut])
def daily(request: Request, filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters)
    return {str(d): climod._bucket_dict(b) for d, b in sorted(report.by_day.items())}


@router.get("/hourly", response_model=dict[str, schemas.BucketOut])
def hourly(request: Request, filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters)
    return {str(h): climod._bucket_dict(b) for h, b in sorted(report.by_hour.items())}


_BREAKDOWNS = {"model": "by_model", "project": "by_project",
               "source": "by_source", "surface": "by_source"}


@router.get("/breakdown/{dim}", response_model=dict[str, schemas.BucketOut])
def breakdown(dim: str, request: Request, filters: Filters = Depends(get_filters)):
    attr = _BREAKDOWNS.get(dim)
    if attr is None:
        raise HTTPException(404, f"unknown breakdown dimension {dim!r}, "
                                  f"expected one of {sorted(_BREAKDOWNS)}")
    report, *_ = _report(request, filters)
    return {k: climod._bucket_dict(v) for k, v in getattr(report, attr).items()}


@router.get("/sessions", response_model=list[schemas.SessionOut])
def sessions_list(request: Request, limit: int = Query(40, ge=1, le=500), cursor: str | None = None,
                   q: str | None = Query(None, max_length=500),
                   filters: Filters = Depends(get_filters)):
    source = None if filters.source == "all" else filters.source
    with store.connect() as conn:
        try:
            return store.sessions(
                conn, limit=limit, cursor=cursor,
                source=source, project=filters.project, model=filters.model,
                date_from=filters.date_from, date_to=filters.date_to,
                tz=request.app.state.tz, search=q,
            )
        except ValueError as exc:
            raise HTTPException(400, "invalid session cursor") from exc


@router.get("/sessions/{session_id}", response_model=schemas.SessionDetailOut)
def session_detail(session_id: str):
    with store.connect() as conn:
        row = store.session_detail(conn, session_id)
    if row is None:
        raise HTTPException(404, f"no session {session_id!r}")
    return row


@router.get("/session-facets", response_model=schemas.SessionFacetsOut)
def session_facets():
    with store.connect() as conn:
        def values(local_column: str, cloud_column: str) -> list[str]:
            rows = conn.execute(
                f"SELECT DISTINCT {local_column} AS value FROM turns "
                f"UNION SELECT DISTINCT {cloud_column} FROM sessions WHERE harvested = 1"
            )
            return sorted(row["value"] for row in rows if row["value"])

        return schemas.SessionFacetsOut(
            sources=values("source", "surface"),
            projects=values("project", "project"),
            models=values("model", "model"),
        )


@router.get("/blocks", response_model=list[schemas.BlockOut])
def blocks(request: Request, filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters)
    return [
        schemas.BlockOut(
            start=b.start.isoformat(), end=b.end.isoformat(), is_active=b.is_active,
            tokens=b.bucket.total_tokens, cost_usd=round(b.bucket.cost, 6),
            tokens_per_minute=round(b.tokens_per_minute, 2), models=sorted(b.models),
        )
        for b in report.blocks
    ]


@router.get("/quota", response_model=schemas.QuotaOut | None)
def quota_endpoint(request: Request, filters: Filters = Depends(get_filters)):
    _, q, *_ = _report(request, filters)
    return _quota_out(q)


@router.get("/health", response_model=schemas.HealthOut)
def health():
    with store.connect() as conn:
        info = store.summary(conn)
    return schemas.HealthOut(
        **info,
        note="turns/cloud reflect the local store only; plan utilization "
             "(GET /api/quota) is the one account-wide number.",
    )


@router.post("/sync", response_model=schemas.SyncResultOut)
def sync(request: Request):
    from ..aggregate import cost_of
    from ..ingest import discover

    cfg, tz = request.app.state.cfg, request.app.state.tz
    files = discover(cfg)
    overrides = cfg.get("pricing_overrides")
    with store.connect() as conn:
        result = store.sync_files(conn, files, tz, lambda t: cost_of(t, overrides))
    return schemas.SyncResultOut(
        parsed=result.parsed, skipped=result.skipped, turns=result.turns,
        duplicates=result.duplicates, prompts=result.prompts,
    )


@router.post("/harvest", response_model=schemas.HarvestResultOut)
async def harvest(request: Request):
    payload = await request.json()
    if isinstance(payload, dict) and "ccr" in payload:
        payload = payload["ccr"]
    with store.connect() as conn:
        written, skipped = store.upsert_cloud_sessions(conn, payload)
    return schemas.HarvestResultOut(written=written, skipped=skipped)


@router.get("/export")
def export(request: Request, format: str = Query("json"),
            filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters)
    if format == "csv":
        return PlainTextResponse(climod.to_csv(report), media_type="text/csv")
    if format == "json":
        return PlainTextResponse(
            jsonlib.dumps(climod.serialize(report), indent=2),
            media_type="application/json",
        )
    raise HTTPException(400, f"format must be csv or json, got {format!r}")
