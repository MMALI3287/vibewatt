"""Typed API endpoints shared by the dashboard."""

from __future__ import annotations

import json as jsonlib
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from .. import cli as climod
from .. import store
from ..analysis.models import Kind, Severity
from . import schemas
from .dependencies import Filters, get_filters

router = APIRouter(prefix="/api")


@router.get("/wrapped", response_model=schemas.WrappedOut)
def wrapped(
    request: Request,
    year: int | None = Query(None, ge=1970, le=9998),
    filters: Filters = Depends(get_filters),
):
    from ..analysis.wrapped import build

    request.app.state.ensure_synced()
    with store.connect() as conn:
        return build(
            conn,
            request.app.state.cfg,
            request.app.state.tz,
            year or datetime.now(request.app.state.tz).year,
            source=filters.source,
            project=_raw_projects(request, conn, filters),
            model=filters.model,
        )


@router.get("/alerts", response_model=schemas.AlertsOut)
def alerts(request: Request):
    from ..analysis.alerts import evaluate

    cfg = request.app.state.cfg
    with store.connect() as conn:
        return evaluate(
            conn,
            request.app.state.tz,
            overrides=cfg.get("pricing_overrides"),
            session_hours=cfg.get("session_length_hours", 5),
        )


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
def concierge(
    request: Request, project: str = Query(..., min_length=1, max_length=500)
):
    from ..concierge import build

    with store.connect() as conn:
        return build(conn, request.app.state.cfg, project)


def _findings(
    request: Request, filters: Filters, kind, severity, include_dismissed, force: bool
):
    from ..analysis import current

    with store.connect() as conn:
        result = current(
            conn,
            request.app.state.tz,
            force=force,
            date_from=filters.date_from,
            date_to=filters.date_to,
            source=filters.source,
            project=_raw_projects(request, conn, filters),
            model=filters.model,
            overrides=request.app.state.cfg.get("pricing_overrides"),
        )
    result = dict(result)
    result["findings"] = [
        f
        for f in result["findings"]
        if (include_dismissed or not f["dismissed"])
        and (not kind or f["kind"] == kind)
        and (not severity or f["severity"] == severity)
    ]
    return result


@router.get("/findings", response_model=schemas.AnalysisOut)
def findings(
    request: Request,
    filters: Filters = Depends(get_filters),
    kind: Kind | None = None,
    severity: Severity | None = None,
    include_dismissed: bool = False,
):
    """The stored snapshot; recomputed only after the store changes (A-092)."""
    request.app.state.ensure_synced()
    return _findings(request, filters, kind, severity, include_dismissed, force=False)


@router.post("/analysis", response_model=schemas.AnalysisOut)
def run_analysis(
    request: Request,
    filters: Filters = Depends(get_filters),
    kind: Kind | None = None,
    severity: Severity | None = None,
    include_dismissed: bool = False,
):
    """Recompute now, whatever the snapshot says."""
    return _findings(request, filters, kind, severity, include_dismissed, force=True)


@router.get(
    "/findings/{finding_id}",
    response_model=schemas.FindingOut,
    responses={404: {"description": "No such finding"}},
)
def finding_detail(finding_id: str):
    """One finding, for the deep-linkable finding modal (A-046)."""
    with store.connect() as conn:
        found = store.finding(conn, finding_id)
    if found is None:
        raise HTTPException(404, "finding not found")
    return found


@router.post("/findings/{finding_id}/dismiss", response_model=schemas.DismissFindingOut)
def dismiss_finding(finding_id: str, body: schemas.DismissFindingIn):
    with store.connect() as conn:
        if not store.dismiss_finding(conn, finding_id, body.dismissed):
            raise HTTPException(404, "finding not found")
    return schemas.DismissFindingOut(id=finding_id, dismissed=body.dismissed)


def _labels(request: Request, conn) -> dict[str, str]:
    from ..projects import project_map

    return project_map(conn, request.app.state.cfg)


def _raw_projects(request: Request, conn, filters: Filters) -> list[str] | None:
    """The shown project name in a filter, back to the raw names behind it (A-029)."""
    from ..projects import resolve

    return resolve(
        _labels(request, conn),
        filters.project,
        bool(request.app.state.cfg.get("mask_projects")),
    )


def _report(request: Request, filters: Filters, parts: frozenset[str] | None = None):
    request.app.state.ensure_synced()
    return request.app.state.report(
        source=filters.source,
        date_from=filters.date_from,
        date_to=filters.date_to,
        project=filters.project,
        model=filters.model,
        parts=parts,
    )


def _quota_out(
    q, forecasts: list[dict] | None = None, recent: list[dict] | None = None
) -> schemas.QuotaOut | None:
    if q is None:
        return None
    by_series = {(f["key"], f["scope"]): f for f in forecasts or []}
    windows = []
    for w in q.windows:
        f = by_series.get((w.key, w.scope), {})
        windows.append(
            schemas.WindowOut(
                key=w.key,
                label=w.label,
                scope=w.scope,
                source=w.source,
                utilization=w.utilization,
                resets_at=(
                    w.resets_at.isoformat() if w.resets_at else f.get("resets_at")
                ),
                pace_delta=f.get("pace_delta"),
                elapsed_pct=f.get("elapsed_pct"),
                band=f.get("band"),
                note=f.get("note"),
            )
        )
    return schemas.QuotaOut(
        source=q.source,
        fetched_at=q.fetched_at.isoformat(),
        windows=windows,
        recent=recent or [],
        notes=q.notes,
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


# No "surface": it was an alias of source over local logs only, while a surface
# (web, Cowork remote) is mostly what local logs cannot see (A-075).
_BREAKDOWNS = {"model": "by_model", "project": "by_project", "source": "by_source"}


@router.get("/breakdown/{dim}", response_model=dict[str, schemas.BucketOut])
def breakdown(dim: str, request: Request, filters: Filters = Depends(get_filters)):
    attr = _BREAKDOWNS.get(dim)
    if attr is None:
        raise HTTPException(
            404,
            f"unknown breakdown dimension {dim!r}, "
            f"expected one of {sorted(_BREAKDOWNS)}",
        )
    report, *_ = _report(request, filters, frozenset({attr}))
    return {k: climod._bucket_dict(v) for k, v in getattr(report, attr).items()}


@router.get("/sessions", response_model=list[schemas.SessionOut])
def sessions_list(
    request: Request,
    limit: int = Query(40, ge=1, le=500),
    cursor: str | None = None,
    q: str | None = Query(None, max_length=500),
    filters: Filters = Depends(get_filters),
):
    source = None if filters.source == "all" else filters.source
    with store.connect() as conn:
        try:
            return store.sessions(
                conn,
                limit=limit,
                cursor=cursor,
                source=source,
                project=_raw_projects(request, conn, filters),
                model=filters.model,
                date_from=filters.date_from,
                date_to=filters.date_to,
                tz=request.app.state.tz,
                search=q,
                labels=_labels(request, conn),
            )
        except ValueError as exc:
            raise HTTPException(400, "invalid session cursor") from exc


@router.get("/sessions/{session_id}", response_model=schemas.SessionDetailOut)
def session_detail(session_id: str, request: Request):
    with store.connect() as conn:
        row = store.session_detail(conn, session_id)
        labels = _labels(request, conn)
    if row is None:
        raise HTTPException(404, f"no session {session_id!r}")
    # Masked names everywhere, including a session's own detail (A-030).
    row["project"] = labels.get(row["project"], row["project"])
    for turn in row["turns"]:
        turn["project"] = labels.get(turn["project"], turn["project"])
    return row


@router.get("/session-facets", response_model=schemas.SessionFacetsOut)
def session_facets(request: Request):
    with store.connect() as conn:

        def values(local_column: str, cloud_column: str) -> list[str]:
            rows = conn.execute(
                f"SELECT DISTINCT {local_column} AS value FROM turns "
                f"UNION SELECT DISTINCT {cloud_column} FROM sessions WHERE harvested = 1"
            )
            return sorted(row["value"] for row in rows if row["value"])

        labels = _labels(request, conn)
        return schemas.SessionFacetsOut(
            sources=values("source", "surface"),
            projects=sorted({labels.get(p, p) for p in values("project", "project")}),
            models=values("model", "model"),
        )


@router.get("/blocks", response_model=list[schemas.BlockOut])
def blocks(
    request: Request,
    filters: Filters = Depends(get_filters),
    limit: int = Query(100, ge=1, le=5000),
):
    report, *_ = _report(request, filters, frozenset({"blocks"}))
    # The active block first, then newest first (A-074).
    ordered = sorted(
        report.blocks, key=lambda b: (not b.is_active, -b.start.timestamp())
    )
    return [
        schemas.BlockOut(
            start=b.start.isoformat(),
            end=b.end.isoformat(),
            is_active=b.is_active,
            tokens=b.bucket.total_tokens,
            cost_usd=round(b.bucket.cost, 6),
            tokens_per_minute=round(b.tokens_per_minute, 2),
            models=sorted(b.models),
        )
        for b in ordered[:limit]
    ]


@router.get("/quota", response_model=schemas.QuotaOut | None)
def quota_endpoint(request: Request, filters: Filters = Depends(get_filters)):
    from datetime import timedelta

    from .. import quota
    from ..analysis.forecast import forecast

    # Never calls the endpoint: a page load must not spend the account's
    # rate limit. The background sync does that, throttled.
    q, _ = quota.read(request.app.state.cfg, allow_fetch=False)
    if q is None:
        return None
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    with store.connect() as conn:
        forecasts = forecast(conn)
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT ts, key, scope, utilization, resets_at, source FROM quota_samples"
                " WHERE ts >= ? ORDER BY ts DESC LIMIT 500",
                (since,),
            )
        ]
    return _quota_out(q, forecasts, recent)


@router.get("/reconciliation", response_model=schemas.ReconciliationOut)
def reconciliation(request: Request, filters: Filters = Depends(get_filters)):
    """Why these numbers differ from Claude's own Stats (A-116, A-125).

    Date filters apply. Source, project and model do not: the Stats have none.
    """
    request.app.state.ensure_synced()
    with store.connect() as conn:
        return store.reconciliation(
            conn, request.app.state.tz, filters.date_from, filters.date_to
        )


@router.get("/health", response_model=schemas.HealthOut)
def health():
    with store.connect() as conn:
        info = store.summary(conn)
        coverage = store.coverage(conn)
    return schemas.HealthOut(
        **info,
        coverage=coverage,
        note="turns/cloud reflect the local store only; plan utilization "
        "(GET /api/quota) is the one account-wide number.",
    )


def _sync_out(result) -> dict:
    return {
        "parsed": result.parsed,
        "skipped": result.skipped,
        "turns": result.turns,
        "duplicates": result.duplicates,
        "prompts": result.prompts,
        "unreadable": result.unreadable,
    }


@router.post(
    "/sync",
    response_model=schemas.SyncResultOut,
    responses={
        200: {
            "content": {"application/x-ndjson": {}},
            "description": "With `Accept: application/x-ndjson`, one"
            " `{done, total}` line per batch, then the result.",
        },
        409: {"description": "A sync is already running"},
    },
)
def sync(request: Request):
    import queue
    import threading

    from ..ingest import discover

    cfg, tz = request.app.state.cfg, request.app.state.tz
    lock = request.app.state.sync_lock
    # One sync at a time: a second one is refused, not queued behind a lock (A-056).
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "a sync is already running")
    if "application/x-ndjson" not in request.headers.get("accept", ""):
        try:
            result = climod.sync_store(cfg, tz, discover(cfg))
            request.app.state.synced = True
        finally:
            lock.release()
        return _sync_out(result)

    lines: queue.Queue = queue.Queue()

    def run() -> None:
        try:
            result = climod.sync_store(
                cfg,
                tz,
                discover(cfg),
                progress=lambda done, total: lines.put({"done": done, "total": total}),
            )
            request.app.state.synced = True
            lines.put({"result": _sync_out(result)})
        except Exception as exc:  # noqa: BLE001 - reported in the stream, not a broken response
            lines.put({"error": str(exc)})
        finally:
            lock.release()
            lines.put(None)

    threading.Thread(target=run, daemon=True).start()

    def stream():
        while (item := lines.get()) is not None:
            yield jsonlib.dumps(item) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


HARVEST_MAX_BYTES = 20 * 1024 * 1024


@router.post(
    "/harvest",
    response_model=schemas.HarvestResultOut,
    responses={
        400: {"description": "Not a session listing"},
        413: {"description": "Body larger than 20 MiB"},
    },
)
async def harvest(request: Request, body: list[dict] | schemas.HarvestEnvelope):
    # The body is typed and size-capped (A-031, A-102).
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > HARVEST_MAX_BYTES:
        raise HTTPException(413, "session listing larger than 20 MiB")
    with store.connect() as conn:
        counts = store.upsert_cloud_sessions(conn, schemas.harvest_entries(body))
    if not any(counts.values()):
        raise HTTPException(400, "no session entries found in the body")
    return counts


@router.get(
    "/export",
    responses={
        200: {
            "content": {
                "text/csv": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "object"}},
            },
            "description": "Daily rows by model as CSV, or the summary as JSON.",
        }
    },
)
def export(
    request: Request,
    format: Literal["json", "csv"] = Query("json"),
    filters: Filters = Depends(get_filters),
):
    report, *_ = _report(request, filters)
    if format == "csv":
        return PlainTextResponse(climod.to_csv(report), media_type="text/csv")
    return PlainTextResponse(
        jsonlib.dumps(climod.serialize(report), indent=2),
        media_type="application/json",
    )
