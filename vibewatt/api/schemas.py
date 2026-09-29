"""Pydantic response models. Field names mirror `cli.serialize()`'s existing
JSON shape on purpose — that shape is already the proven contract the `json`
command and the old dashboard emit, so the API does not invent a second one."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..analysis.models import Kind, Severity


class ProvenanceOut(BaseModel):
    usage: Literal["official", "computed_local", "cloud_reported", "estimate"]
    cost: Literal["official", "computed_local", "cloud_reported", "estimate"]
    scope: str
    as_of: str | None


class ReportContextOut(BaseModel):
    today: str
    timezone: str
    day_start_hour: int
    as_of: str | None


class PlanComparisonOut(BaseModel):
    period_start: str
    period_end: str
    local_cost_usd: float
    monthly_plan_usd: float
    multiple: float | None
    unpriced: int


class BucketOut(BaseModel):
    responses: int
    input: int
    cache_write_5m: int
    cache_write_1h: int
    cache_read: int
    output: int
    thinking: int
    web_fetch: int = 0
    code_execution: int = 0
    code_execution_cost: Literal["unavailable", "not_used"] = "not_used"
    nonstandard_iterations: int = 0
    context_premium_unknown: int = 0
    web_searches: int
    cost_usd: float
    # Responses on a model with no known rate. cost_usd excludes them, so a row
    # with unpriced > 0 is a lower bound, not a price (A-026).
    unpriced: int = 0


class ActiveBlockOut(BaseModel):
    start: str
    end: str
    tokens: int
    cost_usd: float
    tokens_per_minute: float
    projected_tokens: int
    projected_cost_usd: float


class SummaryOut(BaseModel):
    provenance: ProvenanceOut | None = None
    plan_comparison: PlanComparisonOut | None = None
    total: BucketOut
    by_day: dict[str, BucketOut]
    by_model: dict[str, BucketOut]
    by_source: dict[str, BucketOut]
    by_project: dict[str, BucketOut]
    by_day_model: dict[str, BucketOut]
    by_hour: dict[str, BucketOut]
    sessions: int
    unknown_models: list[str]
    restored_days: list[str]
    cache_hit_rate: float
    active_block: ActiveBlockOut | None = None


class BandOut(BaseModel):
    p10: float
    p50: float
    p90: float


class WindowOut(BaseModel):
    key: str
    label: str
    scope: str
    source: str
    utilization: float
    resets_at: str | None
    # Used % minus elapsed % of the window; positive is ahead of an even spend.
    pace_delta: float | None = None
    elapsed_pct: float | None = None
    # Where this window ends at reset, from this account's past windows.
    band: BandOut | None = None
    note: str | None = None


class QuotaSampleOut(BaseModel):
    ts: str
    key: str
    scope: str
    utilization: float
    resets_at: str | None
    source: str


class QuotaOut(BaseModel):
    provenance: ProvenanceOut | None = None
    source: str
    fetched_at: str
    windows: list[WindowOut]
    recent: list[QuotaSampleOut] = []
    notes: list[str] = []


class SessionOut(BaseModel):
    provenance: ProvenanceOut | None = None
    id: str
    title: str
    surface: str | None
    project: str | None
    model: str | None
    started: str | None
    ended: str | None
    tokens: int
    cost: float
    harvested: bool
    cursor: str | None = None
    unpriced_turns: int = 0
    context_used: int | None = None
    context_max: int | None = None


class SessionFacetsOut(BaseModel):
    sources: list[str]
    projects: list[str]
    models: list[str]


class TurnOut(BaseModel):
    msg_id: str
    request_id: str
    ts: str
    day: str
    source: str
    project: str
    model: str
    input: int
    cache_5m: int
    cache_1h: int
    cache_read: int
    output: int
    thinking: int
    web_search: int
    sidechain: int
    fast: int
    geo: str | None
    cost: float | None


class SessionDetailOut(SessionOut):
    turns: list[TurnOut]


class BlockOut(BaseModel):
    provenance: ProvenanceOut | None = None
    start: str
    end: str
    is_active: bool
    tokens: int
    cost_usd: float
    tokens_per_minute: float
    models: list[str]


class StoreTurnsOut(BaseModel):
    n: int
    lo: str | None
    hi: str | None
    cost: float | None


class StoreCloudOut(BaseModel):
    n: int
    cost: float | None


class SurfaceCountOut(BaseModel):
    surface: str | None
    n: int
    cost: float | None


class CountOut(BaseModel):
    n: int


class SourceCoverageOut(BaseModel):
    source: str
    files: int
    first_day: str | None
    last_day: str | None


class GapOut(BaseModel):
    start: str
    end: str
    days: int


class CoverageOut(BaseModel):
    sources: list[SourceCoverageOut]
    # Spans of 7+ days with no local turn: no activity, or logs pruned before
    # the store existed. The store cannot tell which.
    gaps: list[GapOut]
    dropped_records: dict[str, int]


class HealthOut(BaseModel):
    turns: StoreTurnsOut
    cloud: StoreCloudOut
    cloud_by_surface: list[SurfaceCountOut]
    prompts: CountOut  # session titles; the name predates them
    last_harvest: str | None
    last_sync: str | None
    coverage: CoverageOut
    note: str


class SyncResultOut(BaseModel):
    parsed: int
    skipped: int
    turns: int
    duplicates: int
    prompts: int
    unreadable: int = 0


class HarvestEnvelope(BaseModel):
    """A session listing wrapped as `{"data": [...]}` or `{"ccr": ...}`."""

    model_config = {"extra": "allow"}

    data: list[dict] | None = None
    ccr: list[dict] | dict | None = None


def harvest_entries(body: list[dict] | HarvestEnvelope) -> list[dict]:
    """Session dicts from any of the accepted listing shapes."""
    if isinstance(body, HarvestEnvelope):
        if body.ccr is not None:
            inner = body.ccr.get("data") if isinstance(body.ccr, dict) else body.ccr
        else:
            inner = body.data
        body = inner or []
    return [e for e in body if isinstance(e, dict)]


class HarvestResultOut(BaseModel):
    written: int
    skipped: int  # no usage block: an unstarted session is not free work
    rejected_no_id: int = 0
    skipped_environment: int = 0  # not a cloud or Cowork remote session


class FindingOut(BaseModel):
    id: str
    kind: Kind
    rule: str
    severity: Severity
    subject: str
    day: str | None
    title: str
    detail: str
    coverage: str
    session_id: str | None
    savings_usd: float | None
    metrics: dict[str, float | str | None]
    dismissed: bool
    created_at: str


class DedupedFiguresOut(BaseModel):
    responses: int
    input_output_tokens: int
    all_tokens: int
    sessions: int


class StatsFiguresOut(BaseModel):
    messages: int
    tokens: int
    sessions: int


class ReconciliationOut(BaseModel):
    source: str
    deduped: DedupedFiguresOut
    # What Claude's own Stats would show for the same days. A comparison only.
    stats_equivalent: StatsFiguresOut
    token_ratio: float | None
    reasons: list[str]
    session_definition: str


class AnalysisOut(BaseModel):
    findings: list[FindingOut]
    notes: list[str]
    # Why anomaly detection could not run, shown in the anomaly group (A-093).
    anomaly_notes: list[str] = []
    analyzed_at: str


class DismissFindingIn(BaseModel):
    dismissed: bool = True


class DismissFindingOut(BaseModel):
    id: str
    dismissed: bool


class WrappedProjectOut(BaseModel):
    name: str
    cost_usd: float
    tokens: int


class WrappedModelMonthOut(BaseModel):
    month: str
    model: str
    cost_usd: float
    tokens: int


class WrappedSessionOut(BaseModel):
    id: str
    title: str
    cost_usd: float
    tokens: int
    harvested: bool


class WrappedOut(BaseModel):
    provenance_by_source: dict[str, ProvenanceOut] = {}
    year: int
    timezone: str
    local_summary: SummaryOut
    stored_cost_usd: float
    harvested_cost_usd: float
    stored_tokens: int
    stored_sessions: int
    unpriced_turns: int
    annual_plan_usd: float | None
    api_equivalent_multiple: float | None
    busiest_day: str | None
    busiest_hour: int | None
    longest_streak: int
    top_projects: list[WrappedProjectOut]
    model_months: list[WrappedModelMonthOut]
    biggest_session: WrappedSessionOut | None
    cache_savings_usd: float | None
    notes: list[str]


class AlertOut(BaseModel):
    id: str
    kind: str
    title: str
    detail: str
    created_at: str


class AlertBlockOut(BaseModel):
    tokens_per_minute: float
    cost_per_minute: float


class AlertsOut(BaseModel):
    alerts: list[AlertOut]
    new_count: int
    active_block: AlertBlockOut | None
    notes: list[str]


class WeeklySummaryOut(BaseModel):
    status: str
    text: str | None
    detail: str
    start: str | None
    end: str | None


class ServiceStatusOut(BaseModel):
    indicator: str
    description: str
    url: str


class ConciergeOut(BaseModel):
    project: str
    text: str
    notes: list[str]
