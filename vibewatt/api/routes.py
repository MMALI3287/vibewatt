"""Typed API endpoints shared by the dashboard."""

from __future__ import annotations

import json as jsonlib
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .. import cli as climod
from .. import store
from ..analysis import analyze
from ..analysis.models import Kind, Severity
from . import schemas
from .dependencies import Filters, get_filters

router = APIRouter(prefix="/api")


@router.get("/wrapped", response_model=schemas.WrappedOut)
def wrapped(request: Request, year: int | None = Query(None, ge=1, le=9998),
            filters: Filters = Depends(get_filters)):
    from ..analysis.wrapped import build

    request.app.state.ensure_synced()
    with store.connect() as conn:
        return build(conn, request.app.state.cfg, request.app.state.tz,
                     year or datetime.now(request.app.state.tz).year,
                     source=filters.source, project=filters.project, model=filters.model)


@router.get("/alerts", response_model=schemas.AlertsOut)
def alerts(request: Request):
    from ..analysis.alerts import evaluate

    cfg = request.app.state.cfg
    with store.connect() as conn:
        return evaluate(conn, request.app.state.tz, overrides=cfg.get("pricing_overrides"),
                        session_hours=cfg.get("session_length_hours", 5))


@router.get("/status", response_model=schemas.ServiceStatusOut | None)
def service_status(request: Request):
    from ..service_status import read

    if request.app.state.cfg.get("offline"):
        return None
    return read()


@router.post("/weekly-summary", response_model=schemas.WeeklySummaryOut)
def weekly_summary(request: Request):
    from ..weekly import generate

    request.app.state.ensure_synced()
    return generate(request.app.state.cfg, request.app.state.tz)


@router.get("/concierge", response_model=schemas.ConciergeOut)
def concierge(request: Request, project: str = Query(..., min_length=1, max_length=500)):
    from ..concierge import build

    with store.connect() as conn:
        return build(conn, request.app.state.cfg, project)


@router.get("/findings", response_model=schemas.AnalysisOut)
@router.post("/analysis", response_model=schemas.AnalysisOut)
def findings(
    request: Request, filters: Filters = Depends(get_filters),
    kind: Kind | None = None, severity: Severity | None = None,
    include_dismissed: bool = False,
):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise HTTPException(400, "from must not be after to")
    with store.connect() as conn:
        result = analyze(
            conn, request.app.state.tz, date_from=filters.date_from, date_to=filters.date_to,
            source=filters.source, project=filters.project, model=filters.model,
            overrides=request.app.state.cfg.get("pricing_overrides"),
        )
    result["findings"] = [f for f in result["findings"]
                          if (include_dismissed or not f["dismissed"])
                          and (not kind or f["kind"] == kind)
                          and (not severity or f["severity"] == severity)]
    return result


@router.post("/findings/{finding_id}/dismiss", response_model=schemas.DismissFindingOut)
def dismiss_finding(finding_id: str, body: schemas.DismissFindingIn):
    with store.connect() as conn:
        if not store.dismiss_finding(conn, finding_id, body.dismissed):
            raise HTTPException(404, "finding not found")
    return schemas.DismissFindingOut(id=finding_id, dismissed=body.dismissed)


def _report(request: Request, filters: Filters, parts: frozenset[str] | None = None):
    request.app.state.ensure_synced()
    return request.app.state.report(
        source=filters.source, date_from=filters.date_from, date_to=filters.date_to,
        project=filters.project, model=filters.model, parts=parts,
    )


def _quota_out(q, forecasts: list[dict] | None = None,
               recent: list[dict] | None = None) -> schemas.QuotaOut | None:
    if q is None:
        return None
    by_series = {(f["key"], f["scope"]): f for f in forecasts or []}
    windows = []
    for w in q.windows:
        f = by_series.get((w.key, w.scope), {})
        windows.append(schemas.WindowOut(
            key=w.key, label=w.label, scope=w.scope, source=w.source,
            utilization=w.utilization,
            resets_at=(w.resets_at.isoformat() if w.resets_at
                       else f.get("resets_at")),
            pace_delta=f.get("pace_delta"), elapsed_pct=f.get("elapsed_pct"),
            band=f.get("band"), note=f.get("note"),
        ))
    return schemas.QuotaOut(
        source=q.source, fetched_at=q.fetched_at.isoformat(), windows=windows,
        recent=recent or [], notes=q.notes,
    )


@router.get("/summary", response_model=schemas.SummaryOut)
def summary(request: Request, filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters)
    return climod.serialize(report)


@router.get("/daily", response_model=dict[str, schemas.BucketOut])
def daily(request: Request, filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters, frozenset({"by_day"}))
    return {str(d): climod._bucket_dict(b) for d, b in sorted(report.by_day.items())}


@router.get("/hourly", response_model=dict[str, schemas.BucketOut])
def hourly(request: Request, filters: Filters = Depends(get_filters)):
    report, *_ = _report(request, filters, frozenset({"by_hour"}))
    return {str(h): climod._bucket_dict(b) for h, b in sorted(report.by_hour.items())}


_BREAKDOWNS = {"model": "by_model", "project": "by_project",
               "source": "by_source", "surface": "by_source"}


@router.get("/breakdown/{dim}", response_model=dict[str, schemas.BucketOut])
def breakdown(dim: str, request: Request, filters: Filters = Depends(get_filters)):
    attr = _BREAKDOWNS.get(dim)
    if attr is None:
        raise HTTPException(404, f"unknown breakdown dimension {dim!r}, "
                                  f"expected one of {sorted(_BREAKDOWNS)}")
    report, *_ = _report(request, filters, frozenset({attr}))
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
    report, *_ = _report(request, filters, frozenset({"blocks"}))
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
    from datetime import timedelta, timezone

    from .. import quota
    from ..analysis.forecast import forecast

    # Never calls the endpoint: a page load must not spend the account's
    # rate limit. The background sync does that, throttled.
    q, _ = quota.read(request.app.state.cfg, allow_fetch=False)
    if q is None:
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with store.connect() as conn:
        forecasts = forecast(conn)
        recent = [dict(r) for r in conn.execute(
            "SELECT ts, key, scope, utilization, resets_at, source FROM quota_samples"
            " WHERE ts >= ? ORDER BY ts DESC LIMIT 500", (since,))]
    return _quota_out(q, forecasts, recent)


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
    from ..ingest import discover

    cfg, tz = request.app.state.cfg, request.app.state.tz
    with request.app.state.sync_lock:
        result = climod.sync_store(cfg, tz, discover(cfg))
        request.app.state.synced = True
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
