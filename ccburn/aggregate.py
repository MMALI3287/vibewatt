"""Cost attribution, roll-ups and rate-limit windows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .pricing import MILLION, WEB_SEARCH_PER_CALL, rate_for
from .sources import Turn


@dataclass
class Bucket:
    turns: int = 0
    input: int = 0
    cache_5m: int = 0
    cache_1h: int = 0
    cache_read: int = 0
    output: int = 0
    thinking: int = 0
    web_searches: int = 0
    cost: float = 0.0
    unpriced: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input + self.cache_5m + self.cache_1h + self.cache_read + self.output

    @property
    def cache_write(self) -> int:
        return self.cache_5m + self.cache_1h

    @property
    def cache_hit_rate(self) -> float:
        """Share of read-side input tokens served from cache."""
        served = self.cache_read + self.input + self.cache_write
        return self.cache_read / served if served else 0.0

    def add(self, turn: Turn, cost: float | None) -> None:
        self.turns += 1
        self.input += turn.input
        self.cache_5m += turn.cache_5m
        self.cache_1h += turn.cache_1h
        self.cache_read += turn.cache_read
        self.output += turn.output
        self.thinking += turn.thinking
        self.web_searches += turn.web_searches
        if cost is None:
            self.unpriced += 1
        else:
            self.cost += cost


def cost_of(turn: Turn, overrides: dict | None = None) -> float | None:
    rate = rate_for(turn.model, fast=turn.fast, geo=turn.geo, overrides=overrides)
    if rate is None:
        return None
    return (
        turn.input * rate.input
        + turn.cache_5m * rate.cache_5m
        + turn.cache_1h * rate.cache_1h
        + turn.cache_read * rate.cache_read
        + turn.output * rate.output
    ) / MILLION + turn.web_searches * WEB_SEARCH_PER_CALL


@dataclass
class Block:
    """One rate-limit window: a run of activity bounded by session_length."""

    start: datetime
    end: datetime
    bucket: Bucket = field(default_factory=Bucket)
    models: set = field(default_factory=set)
    last_activity: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.start <= datetime.now(timezone.utc) < self.end

    @property
    def elapsed_minutes(self) -> float:
        end = min(datetime.now(timezone.utc), self.end)
        return max(1.0, (end - self.start).total_seconds() / 60)

    @property
    def tokens_per_minute(self) -> float:
        return self.bucket.total_tokens / self.elapsed_minutes

    @property
    def cost_per_minute(self) -> float:
        return self.bucket.cost / self.elapsed_minutes

    def project_to_end(self) -> tuple[int, float]:
        """Extrapolate this window's totals to its close at the current rate."""
        minutes_left = max(0.0, (self.end - datetime.now(timezone.utc)).total_seconds() / 60)
        return (
            self.bucket.total_tokens + int(self.tokens_per_minute * minutes_left),
            self.bucket.cost + self.cost_per_minute * minutes_left,
        )


def build_blocks(turns: list[Turn], hours: int = 5, overrides: dict | None = None) -> list[Block]:
    """Group turns into rate-limit windows.

    A window opens on the hour containing its first turn and runs `hours`. It
    closes when a turn lands past its end, or after a gap of `hours` with no
    activity at all, which is how Claude's own windows lapse.
    """
    if not turns:
        return []
    length = timedelta(hours=hours)
    ordered = sorted(turns, key=lambda t: t.ts)
    blocks: list[Block] = []
    current: Block | None = None

    for turn in ordered:
        if current is None or turn.ts >= current.end or (
            current.last_activity is not None and turn.ts - current.last_activity >= length
        ):
            start = turn.ts.replace(minute=0, second=0, microsecond=0)
            current = Block(start=start, end=start + length)
            blocks.append(current)
        current.bucket.add(turn, cost_of(turn, overrides))
        current.models.add(turn.model)
        current.last_activity = turn.ts
    return blocks


@dataclass
class Report:
    total: Bucket = field(default_factory=Bucket)
    by_day: dict = field(default_factory=lambda: defaultdict(Bucket))
    by_model: dict = field(default_factory=lambda: defaultdict(Bucket))
    by_source: dict = field(default_factory=lambda: defaultdict(Bucket))
    by_project: dict = field(default_factory=lambda: defaultdict(Bucket))
    by_day_model: dict = field(default_factory=lambda: defaultdict(Bucket))
    by_hour: dict = field(default_factory=lambda: defaultdict(Bucket))   # 0-23
    sessions: set = field(default_factory=set)
    unknown_models: set = field(default_factory=set)
    restored_days: set = field(default_factory=set)
    blocks: list = field(default_factory=list)

    @property
    def days(self) -> list[date]:
        return sorted(self.by_day)

    @property
    def active_block(self) -> Block | None:
        for block in reversed(self.blocks):
            if block.is_active:
                return block
        return None

    def month_to_date(self, today: date | None = None) -> Bucket:
        today = today or date.today()
        out = Bucket()
        for day, bucket in self.by_day.items():
            if day.year == today.year and day.month == today.month:
                out.cost += bucket.cost
                out.turns += bucket.turns
        return out

    def streaks(self) -> tuple[int, int]:
        days = self.days
        if not days:
            return 0, 0
        longest = run = 1
        for prev, cur in zip(days, days[1:]):
            run = run + 1 if cur - prev == timedelta(days=1) else 1
            longest = max(longest, run)
        today = date.today()
        current = 0
        if days[-1] in (today, today - timedelta(days=1)):
            current = 1
            for prev, cur in zip(reversed(days[1:]), reversed(days[:-1])):
                if prev - cur == timedelta(days=1):
                    current += 1
                else:
                    break
        return current, longest

    def peak_day(self):
        if not self.by_day:
            return None
        return max(self.by_day.items(), key=lambda kv: kv[1].total_tokens)


def build(
    turns: list[Turn],
    tz=None,
    include_sidechains: bool = True,
    overrides: dict | None = None,
    session_hours: int = 5,
) -> Report:
    report = Report()
    kept: list[Turn] = []
    for turn in turns:
        if turn.sidechain and not include_sidechains:
            continue
        kept.append(turn)
        cost = cost_of(turn, overrides)
        if cost is None:
            report.unknown_models.add(turn.model)
        local = turn.ts.astimezone(tz)
        day = local.date()
        report.total.add(turn, cost)
        report.by_day[day].add(turn, cost)
        report.by_model[turn.model].add(turn, cost)
        report.by_source[turn.source].add(turn, cost)
        report.by_project[turn.project].add(turn, cost)
        report.by_day_model[(day, turn.model)].add(turn, cost)
        report.by_hour[local.hour].add(turn, cost)
        report.sessions.add(turn.session)
    report.blocks = build_blocks(kept, hours=session_hours, overrides=overrides)
    return report
