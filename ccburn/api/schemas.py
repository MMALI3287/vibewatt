"""Pydantic response models. Field names mirror `cli.serialize()`'s existing
JSON shape on purpose — that shape is already the proven contract the `json`
command and the old dashboard emit, so the API does not invent a second one."""

from __future__ import annotations

from pydantic import BaseModel

from ..analysis.models import Kind, Severity


class BucketOut(BaseModel):
    responses: int
    input: int
    cache_write_5m: int
    cache_write_1h: int
    cache_read: int
    output: int
    thinking: int
    web_searches: int
    cost_usd: float


class ActiveBlockOut(BaseModel):
    start: str
    end: str
    tokens: int
    cost_usd: float
    tokens_per_minute: float
    projected_tokens: int
    projected_cost_usd: float


class SummaryOut(BaseModel):
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


class WindowOut(BaseModel):
    label: str
    utilization: float
    resets_at: str | None


class QuotaOut(BaseModel):
    source: str
    fetched_at: str
    windows: list[WindowOut]


class SessionOut(BaseModel):
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
    start: str
    end: str
    is_active: bool
    tokens: int
    cost_usd: float
    tokens_per_minute: float
    models: list[str]


class HealthOut(BaseModel):
    turns: dict
    cloud: dict
    cloud_by_surface: list[dict]
    prompts: dict
    last_harvest: str | None
    note: str


class SyncResultOut(BaseModel):
    parsed: int
    skipped: int
    turns: int
    duplicates: int
    prompts: int


class HarvestResultOut(BaseModel):
    written: int
    skipped: int


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


class AnalysisOut(BaseModel):
    findings: list[FindingOut]
    notes: list[str]
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
