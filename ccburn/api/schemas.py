"""Pydantic response models. Field names mirror `cli.serialize()`'s existing
JSON shape on purpose — that shape is already the proven contract the `json`
command and the old dashboard emit, so the API does not invent a second one."""

from __future__ import annotations

from pydantic import BaseModel


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


class SessionDetailOut(SessionOut):
    turns: list[dict]


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
