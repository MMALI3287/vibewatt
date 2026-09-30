"""Cost attribution, roll-ups and rate-limit windows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

from .config import clock_zone
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
    web_fetch: int = 0
    code_execution: int = 0
    nonstandard_iterations: int = 0
    context_premium_unknown: int = 0
    cost: float = 0.0
    unpriced: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input + self.cache_5m + self.cache_1h + self.cache_read + self.output
        )

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
        self.web_fetch += turn.web_fetch
        self.code_execution += turn.code_execution
        self.nonstandard_iterations += turn.nonstandard_iterations
        from .pricing import context_premium_unknown

        self.context_premium_unknown += int(context_premium_unknown(turn))
        if cost is None:
            self.unpriced += 1
        else:
            self.cost += cost


def cost_of(turn: Turn, overrides: dict | None = None) -> float | None:
    rate = rate_for(
        turn.model,
        fast=turn.fast,
        ts=turn.ts,
        prompt_tokens=turn.input + turn.cache_read + turn.cache_5m + turn.cache_1h,
        geo=turn.geo,
        overrides=overrides,
    )
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
        return self.start <= datetime.now(UTC) < self.end

    @property
    def elapsed_minutes(self) -> float:
        end = min(datetime.now(UTC), self.end)
        return max(1.0, (end - self.start).total_seconds() / 60)

    @property
    def tokens_per_minute(self) -> float:
        return self.bucket.total_tokens / self.elapsed_minutes

    @property
    def cost_per_minute(self) -> float:
        return self.bucket.cost / self.elapsed_minutes

    def project_to_end(self) -> tuple[int, float]:
        """Extrapolate this window's totals to its close at the current rate."""
        minutes_left = max(0.0, (self.end - datetime.now(UTC)).total_seconds() / 60)
        return (
            self.bucket.total_tokens + int(self.tokens_per_minute * minutes_left),
            self.bucket.cost + self.cost_per_minute * minutes_left,
        )


def build_blocks(
    turns: list[Turn], hours: int = 5, overrides: dict | None = None
) -> list[Block]:
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
        if (
            current is None
            or turn.ts >= current.end
            or (
                current.last_activity is not None
                and turn.ts - current.last_activity >= length
            )
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
    by_hour: dict = field(default_factory=lambda: defaultdict(Bucket))  # 0-23
    # (day, source, project, model) -> Bucket. The grain the UI filters over.
    by_cell: dict = field(default_factory=lambda: defaultdict(Bucket))
    sessions: set = field(default_factory=set)
    unknown_models: set = field(default_factory=set)
    restored_days: set = field(default_factory=set)
    blocks: list = field(default_factory=list)
    today: date | None = None  # today in the REPORT's timezone
    subagent: Bucket = field(default_factory=Bucket)  # sidechain turns, tracked apart

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
        today = today or self.today or datetime.now().astimezone().date()
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
        for prev, cur in pairwise(days):
            run = run + 1 if cur - prev == timedelta(days=1) else 1
            longest = max(longest, run)
        today = self.today or datetime.now().astimezone().date()
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
    report.today = datetime.now(tz).date()
    kept: list[Turn] = []
    for turn in turns:
        if turn.sidechain:
            # Subagent spend is real but belongs beside the parent's own figure,
            # not silently folded into it: a few parallel subagents can be most
            # of a session's tokens.
            report.subagent.add(turn, cost_of(turn, overrides))
            if not include_sidechains:
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
        report.by_hour[turn.ts.astimezone(clock_zone(tz)).hour].add(turn, cost)
        report.by_cell[(day, turn.source, turn.project, turn.model)].add(turn, cost)
        report.sessions.add(turn.session)
    report.blocks = build_blocks(kept, hours=session_hours, overrides=overrides)
    return report


def plan_comparison(report: Report, plan_usd: float | None) -> dict | None:
    """What the subscription saved against pay-as-you-go API rates.

    A large API-equivalent figure is the point of a subscription, not a warning.
    Shown without this comparison it reads like a bill, which it is not.
    """
    if not plan_usd:
        return None
    mtd = report.month_to_date().cost
    if mtd <= 0:
        return None
    return {
        "plan_usd": plan_usd,
        "api_equivalent_usd": mtd,
        "saved_usd": mtd - plan_usd,
        "multiple": mtd / plan_usd,
    }


# --- reports from the store ---------------------------------------------------

_SUMS = (
    "SUM(responses) turns, SUM(input) input, SUM(cache_5m) cache_5m,"
    " SUM(cache_1h) cache_1h, SUM(cache_read) cache_read, SUM(output) output,"
    " SUM(thinking) thinking, SUM(web_search) web_searches,"
    " COALESCE(SUM(cost), 0) cost, SUM(unpriced) unpriced,"
    " SUM(web_fetch) web_fetch, SUM(code_execution) code_execution,"
    " SUM(nonstandard_iterations) nonstandard_iterations, SUM(context_premium_unknown) context_premium_unknown"
)
_BUCKET_FIELDS = (
    "turns",
    "input",
    "cache_5m",
    "cache_1h",
    "cache_read",
    "output",
    "thinking",
    "web_searches",
    "web_fetch",
    "code_execution",
    "nonstandard_iterations",
    "context_premium_unknown",
    "cost",
    "unpriced",
)


_CELL_SUMS = (
    "SUM(turns) turns, SUM(input) input, SUM(cache_5m) cache_5m,"
    " SUM(cache_1h) cache_1h, SUM(cache_read) cache_read, SUM(output) output,"
    " SUM(thinking) thinking, SUM(web_searches) web_searches,"
    " COALESCE(SUM(cost), 0) cost, SUM(unpriced) unpriced,"
    " SUM(web_fetch) web_fetch, SUM(code_execution) code_execution,"
    " SUM(nonstandard_iterations) nonstandard_iterations, SUM(context_premium_unknown) context_premium_unknown"
)
REPORT_PARTS = frozenset(
    {
        "by_day",
        "by_model",
        "by_source",
        "by_project",
        "by_day_model",
        "by_hour",
        "by_cell",
        "subagent",
        "sessions",
        "blocks",
    }
)


def _bucket(row) -> Bucket:
    b = Bucket()
    for f in _BUCKET_FIELDS:
        setattr(b, f, row[f] or (0.0 if f == "cost" else 0))
    return b


def _merge_into(target: Bucket, extra: Bucket) -> Bucket:
    for f in _BUCKET_FIELDS:
        setattr(target, f, getattr(target, f) + getattr(extra, f))
    return target


def from_store(
    conn,
    tz=None,
    *,
    source: str = "all",
    date_from: date | None = None,
    date_to: date | None = None,
    project: str | list[str] | None = None,
    model: str | None = None,
    include_sidechains: bool = True,
    session_hours: int = 5,
    overrides: dict | None = None,
    parts: frozenset[str] | None = None,
) -> Report:
    """Build a Report from the rollup table. No log file is read.

    Days come from the store, bucketed in the timezone of the last sync; the
    caller syncs with the report timezone first. `parts` limits the work to
    the Report fields an endpoint serves (see REPORT_PARTS); None builds all.
    """
    from . import store

    if tz is not None:
        store.rebucket(conn, tz)
    want = REPORT_PARTS if parts is None else parts
    report = Report()
    report.today = datetime.now(tz).date()
    where, args = ["1=1"], []
    if source and source != "all":
        where.append("source = ?")
        args.append(source)
    if project is not None:
        from .projects import clause

        sql, values = clause("project", project)
        where.append(sql)
        args.extend(values)
    if model:
        where.append("model = ?")
        args.append(model)
    if date_from:
        where.append("day >= ?")
        args.append(date_from.isoformat())
    if date_to:
        where.append("day <= ?")
        args.append(date_to.isoformat())
    base = " AND ".join(where)
    kept = base if include_sidechains else base + " AND sidechain = 0"

    # One scan of the rollup drops the session and UTC-hour grain; every
    # breakdown then groups the much smaller temp table.
    conn.execute("DROP TABLE IF EXISTS temp.cells")
    conn.execute(
        f"CREATE TEMP TABLE cells AS SELECT day, hour, source, project, model, {_SUMS}"
        f" FROM rollup WHERE {kept} GROUP BY day, hour, source, project, model",
        args,
    )

    def rows(cols: str):
        group = f" GROUP BY {cols}" if cols else ""
        select = f"{cols}, {_CELL_SUMS}" if cols else _CELL_SUMS
        return conn.execute(f"SELECT {select} FROM cells{group}")

    total = rows("").fetchone()
    if total["turns"]:
        report.total = _bucket(total)
    if "by_day" in want:
        for r in rows("day"):
            report.by_day[date.fromisoformat(r["day"])] = _bucket(r)
    for r in rows("model"):
        if "by_model" in want:
            report.by_model[r["model"]] = _bucket(r)
        if r["unpriced"]:
            report.unknown_models.add(r["model"])
    if "by_source" in want:
        for r in rows("source"):
            report.by_source[r["source"]] = _bucket(r)
    if "by_project" in want:
        for r in rows("project"):
            report.by_project[r["project"]] = _bucket(r)
    if "by_day_model" in want:
        for r in rows("day, model"):
            report.by_day_model[(date.fromisoformat(r["day"]), r["model"])] = _bucket(r)
    if "by_hour" in want:
        for r in rows("hour"):
            if r["hour"] is not None:
                report.by_hour[r["hour"]] = _bucket(r)
    if "by_cell" in want:
        for r in rows("day, source, project, model"):
            report.by_cell[
                (date.fromisoformat(r["day"]), r["source"], r["project"], r["model"])
            ] = _bucket(r)
    conn.execute("DROP TABLE temp.cells")
    if "subagent" in want:
        sub = conn.execute(
            f"SELECT {_SUMS} FROM rollup WHERE {base} AND sidechain = 1", args
        ).fetchone()
        if sub["turns"]:
            report.subagent = _bucket(sub)
    if "sessions" in want:
        report.sessions = {
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT session FROM rollup WHERE {kept}", args
            )
        }
    if "blocks" in want:
        report.blocks = _blocks_from_hours(
            conn.execute(
                f"SELECT hr, MIN(first_ts) first_ts, MAX(last_ts) last_ts,"
                f" GROUP_CONCAT(DISTINCT model) models, {_SUMS}"
                f" FROM rollup WHERE {kept} GROUP BY hr ORDER BY hr",
                args,
            ),
            session_hours,
        )
    if (not source or source == "all") and project is None:
        _add_history(conn, report, date_from, date_to, model, overrides)
    return report


def _blocks_from_hours(hour_rows, hours: int) -> list[Block]:
    """build_blocks() over hourly rows. Exact, not an approximation.

    A block ends on an hour boundary, so every turn in one UTC hour falls on
    the same side of it. A gap of `hours` cannot open inside one hour either.
    Comparing each hour's first turn with the previous hour's last turn is
    therefore the same test build_blocks() applies turn by turn.
    """
    length = timedelta(hours=hours)
    blocks: list[Block] = []
    current: Block | None = None
    for r in hour_rows:
        first = datetime.fromisoformat(r["first_ts"])
        last = datetime.fromisoformat(r["last_ts"])
        if (
            current is None
            or first >= current.end
            or (
                current.last_activity is not None
                and first - current.last_activity >= length
            )
        ):
            start = first.replace(minute=0, second=0, microsecond=0)
            current = Block(start=start, end=start + length)
            blocks.append(current)
        _merge_into(current.bucket, _bucket(r))
        current.models.update((r["models"] or "").split(","))
        current.last_activity = last
    return blocks


def _add_history(conn, report: Report, date_from, date_to, model, overrides) -> None:
    """Add what the retired history.json recorded beyond the store, per day and model.

    The store never loses a turn, so this only ever fills days the logs were
    pruned from before the store existed. Taking the excess per field means a
    day half pruned before the first sync gets exactly its missing part. A
    model that cannot be priced today stays unpriced, never $0.
    """
    where, args = ["1=1"], []
    if date_from:
        where.append("day >= ?")
        args.append(date_from.isoformat())
    if date_to:
        where.append("day <= ?")
        args.append(date_to.isoformat())
    if model:
        where.append("model = ?")
        args.append(model)
    cond = " AND ".join(where)
    machine = conn.execute(
        "SELECT value FROM meta WHERE key='history_machine_id'"
    ).fetchone()[0]
    history = [
        dict(r, machine_id=machine)
        for r in conn.execute(f"SELECT * FROM history_days WHERE {cond}", args)
    ]
    history += [
        dict(r)
        for r in conn.execute(f"SELECT * FROM imported_history WHERE {cond}", args)
    ]
    if not history:
        return
    live = {
        (r["machine_id"], r["day"], r["model"]): r
        for r in conn.execute(
            f"SELECT machine_id, day, model, {_SUMS} FROM rollup WHERE {cond} GROUP BY machine_id, day, model",
            args,
        )
    }
    pairs = (
        ("responses", "turns"),
        ("input", "input"),
        ("cache_5m", "cache_5m"),
        ("cache_1h", "cache_1h"),
        ("cache_read", "cache_read"),
        ("output", "output"),
        ("thinking", "thinking"),
        ("web_search", "web_searches"),
    )
    for h in history:
        have = live.get((h["machine_id"], h["day"], h["model"]))
        extra = Bucket()
        for hist_field, field_name in pairs:
            stored = have[field_name] if have else 0
            setattr(extra, field_name, max(0, (h[hist_field] or 0) - (stored or 0)))
        if not (extra.turns or extra.total_tokens):
            continue
        rate = rate_for(h["model"], overrides=overrides)
        if rate is None:
            extra.unpriced = max(1, extra.turns)
            report.unknown_models.add(h["model"])
        else:
            # Price the missing tokens at today's rates. Subtracting repriced
            # store costs from history's old-rate cost would misprice the excess.
            extra.cost = (
                extra.input * rate.input
                + extra.cache_5m * rate.cache_5m
                + extra.cache_1h * rate.cache_1h
                + extra.cache_read * rate.cache_read
                + extra.output * rate.output
            ) / MILLION + extra.web_searches * WEB_SEARCH_PER_CALL
        day = date.fromisoformat(h["day"])
        _merge_into(report.by_day[day], extra)
        _merge_into(report.by_day_model[(day, h["model"])], extra)
        _merge_into(report.by_model[h["model"]], extra)
        _merge_into(report.total, extra)
        report.restored_days.add(day)


METRICS = {
    "cost": ("cost", lambda b: b.cost),
    "total": ("total tokens", lambda b: b.total_tokens),
    "output": ("output tokens", lambda b: b.output),
    "responses": ("responses", lambda b: b.turns),
}
