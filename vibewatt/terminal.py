"""Terminal rendering: KPI row, calendar heatmap, quota bars, tables."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

from .aggregate import Bucket, Report

# Sequential blue, light -> dark, stepped for a dark terminal surface so that
# "near zero" recedes into the background. Five levels plus an empty step.
DARK_RAMP = ["#2f2f2d", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]
LIGHT_RAMP = ["#f0efec", "#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#104281"]

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fg(hex_color: str) -> str:
    r, g, b = _rgb(hex_color)
    return f"\033[38;2;{r};{g};{b}m"


def use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def level(value: float, ceiling: float, steps: int = 5) -> int:
    """Bucket a value into 1..steps; 0 only when there is no activity at all."""
    if value <= 0:
        return 0
    if ceiling <= 0:
        return 1
    # Fourth-root compression: a single 10x day would otherwise flatten the rest.
    ratio = (value / ceiling) ** 0.25
    return max(1, min(steps, int(ratio * steps + 0.999)))


def human(n: float) -> str:
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


def money(n: float) -> str:
    return f"${n:,.2f}" if n >= 0.01 or n == 0 else f"${n:.4f}"


def heatmap(
    report: Report,
    weeks: int = 53,
    color: bool = True,
    ramp: list[str] | None = None,
    today: date | None = None,
) -> str:
    """GitHub-style grid: columns are weeks, rows are weekdays, latest at the right."""
    ramp = ramp or DARK_RAMP
    # The report's own today, in its timezone and day boundary (A-055).
    today = today or report.today or datetime.now().astimezone().date()
    # Anchor the final column to this week, starting weeks on Monday.
    end = today + timedelta(days=(6 - today.weekday()))
    start = end - timedelta(weeks=weeks) + timedelta(days=1)

    ceiling = max((b.total_tokens for b in report.by_day.values()), default=0)
    cell = "■"

    rows: list[str] = []
    labels = {0: "Mon", 2: "Wed", 4: "Fri"}
    month_row = [" "] * weeks
    seen_months: set[tuple[int, int]] = set()
    for w in range(weeks):
        col_start = start + timedelta(weeks=w)
        key = (col_start.year, col_start.month)
        if col_start.day <= 7 and key not in seen_months:
            seen_months.add(key)
            month_row[w] = col_start.strftime("%b")

    # Place each month label at its exact column, so nothing drifts.
    slots = [" "] * (weeks + 4)
    for w, label in enumerate(month_row):
        if label != " " and all(slots[w + i] == " " for i in range(len(label))):
            slots[w : w + len(label)] = list(label)
    header = "    " + "".join(slots).rstrip()
    rows.append(f"{DIM}{header}{RESET}" if color else header)

    for weekday in range(7):
        line = f"{labels.get(weekday, ''):<4}"
        for w in range(weeks):
            day = start + timedelta(weeks=w, days=weekday)
            if day > today:
                line += " "
                continue
            bucket = report.by_day.get(day)
            lvl = level(bucket.total_tokens if bucket else 0, ceiling)
            line += (
                f"{_fg(ramp[lvl])}{cell}{RESET}" if color else (cell if lvl else "·")
            )
        rows.append(line)

    legend = "    Less "
    for i in range(len(ramp)):
        legend += f"{_fg(ramp[i])}{cell}{RESET}" if color else cell
    legend += " More"
    rows.append("")
    rows.append(f"{DIM}{legend}{RESET}" if color else legend)
    return "\n".join(rows)


def _row(label: str, bucket: Bucket, width: int) -> str:
    return (
        f"  {label:<{width}}  {human(bucket.input):>8}  {human(bucket.cache_5m + bucket.cache_1h):>9}"
        f"  {human(bucket.cache_read):>9}  {human(bucket.output):>8}  {money(bucket.cost):>10}"
    )


def table(
    title: str, buckets: dict[str, Bucket], color: bool = True, limit: int | None = None
) -> str:
    if not buckets:
        return ""
    items = sorted(buckets.items(), key=lambda kv: kv[1].cost, reverse=True)
    if limit:
        items = items[:limit]
    width = max(len(k) for k, _ in items)
    width = max(width, len(title))
    head = f"  {title:<{width}}  {'input':>8}  {'cache wr':>9}  {'cache rd':>9}  {'output':>8}  {'cost':>10}"
    lines = [f"{BOLD}{head}{RESET}" if color else head]
    lines.append(
        f"  {'-' * width}  {'-' * 8}  {'-' * 9}  {'-' * 9}  {'-' * 8}  {'-' * 10}"
    )
    for name, bucket in items:
        lines.append(_row(name, bucket, width))
    return "\n".join(lines)


def summary(report: Report, color: bool = True) -> str:
    current, longest = report.streaks()
    peak = report.peak_day()
    b = report.total
    pairs = [
        ("input tokens", human(b.input)),
        ("cache write", human(b.cache_5m + b.cache_1h)),
        ("cache read", human(b.cache_read)),
        ("output tokens", human(b.output)),
        ("est. cost", money(b.cost)),
    ]
    extra = [
        ("active days", str(len(report.by_day))),
        ("sessions", str(len(report.sessions))),
        ("responses", str(b.turns)),
        (
            "peak day",
            f"{peak[0]:%d %b %y} ({human(peak[1].total_tokens)})" if peak else "-",
        ),
        ("streak", f"{current}d (best {longest}d)"),
    ]

    def block(items: list[tuple[str, str]]) -> str:
        labels = "  ".join(f"{k.upper():<22}" for k, _ in items)
        values = "  ".join(f"{v:<22}" for _, v in items)
        if color:
            return f"  {DIM}{labels}{RESET}\n  {BOLD}{values}{RESET}"
        return f"  {labels}\n  {values}"

    return block(pairs) + "\n\n" + block(extra)


def bar(fraction: float, width: int = 24, color: bool = True) -> str:
    """A horizontal meter. Colour marks severity; the number carries the value."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    hue = "#1baf7a" if fraction < 0.75 else ("#eda100" if fraction < 0.9 else "#e34948")
    body = "\u2588" * filled + "\u2591" * (width - filled)
    return f"{_fg(hue)}{body}{RESET}" if color else body


def quota_block(quota, color: bool = True, note: str | None = None) -> str:
    """Plan utilization across the whole account, every surface included."""
    if quota is None:
        return ""
    lines = []
    for w in quota.windows:
        left = ""
        if w.remaining_seconds is not None:
            hours, rem = divmod(int(w.remaining_seconds), 3600)
            left = f"resets in {hours}h {rem // 60:02d}m"
        label = f"{w.label:<14}"
        pct = f"{w.utilization:5.1f}%"
        lines.append(
            f"  {label} {bar(w.utilization / 100, color=color)} {pct}   "
            f"{DIM if color else ''}{left}{RESET if color else ''}"
        )
    note = note or f"account-wide, via {quota.source}"
    header = f"  {DIM}{note}{RESET}" if color else f"  {note}"
    return "\n".join([header] + lines)


def block_block(report, color: bool = True) -> str:
    """The live rate-limit window: what it has cost and where it is heading."""
    active = report.active_block
    if active is None:
        return ""
    tokens, cost = active.project_to_end()
    left = max(
        0,
        int((active.end - active.start).total_seconds() / 60 - active.elapsed_minutes),
    )
    rows = [
        (
            f"  window   {active.start:%H:%M} - {active.end:%H:%M}  "
            f"({left}m left of {int((active.end - active.start).total_seconds() // 3600)}h)"
        ),
        f"  so far   {human(active.bucket.total_tokens)} tokens   {money(active.bucket.cost)}",
        f"  rate     {human(active.tokens_per_minute)} tok/min   {money(active.cost_per_minute * 60)}/h",
        f"  at close {human(tokens)} tokens   {money(cost)}",
    ]
    if color:
        rows = [
            f"{r.split('  ', 2)[0]}  {DIM}{r.split('  ', 2)[1]}{RESET}  {r.split('  ', 2)[2]}"
            if r.count("  ") >= 2
            else r
            for r in rows
        ]
    return "\n".join(rows)
