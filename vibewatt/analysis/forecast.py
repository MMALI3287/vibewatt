"""Plan-window pace and projection from this account's own history.

The old burn alert drew a line through the last two samples. Samples arrive in
bursts (a statusline refresh, a page load) and in whole percents, so that line
was flat or wild and the alert almost never fired (A-006, A-048).

Instead, each window reports:

- pace: used % minus elapsed % of the window. Positive means ahead of an even
  spend.
- a P10-P90 band of where it ends at reset, from how much every past window of
  the same kind still grew after the same elapsed fraction. Until enough past
  windows exist the band is None and the note says so.

Resets are detected from the data, never from an assumed schedule: a change of
`resets_at`, a drop in utilization or a gap longer than the window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..quota import _parse_time, label_for, window_length

MIN_HISTORY = 4  # completed windows needed before a band is shown
DROP = 2.0  # a fall of more than this many points is a reset
RESET_JITTER = timedelta(minutes=10)


@dataclass
class Point:
    ts: datetime
    used: float
    resets_at: datetime | None


@dataclass
class Episode:
    points: list[Point] = field(default_factory=list)

    @property
    def resets_at(self) -> datetime | None:
        for p in reversed(self.points):
            if p.resets_at is not None:
                return p.resets_at
        return None

    def start(self, length: timedelta) -> datetime:
        reset = self.resets_at
        return reset - length if reset is not None else self.points[0].ts

    def end(self, length: timedelta) -> datetime:
        reset = self.resets_at
        return reset if reset is not None else self.points[0].ts + length

    @property
    def peak(self) -> float:
        return max(p.used for p in self.points)

    def used_at(self, fraction: float, length: timedelta) -> float:
        """Utilization at an elapsed fraction: the last reading at or before it."""
        start = self.start(length)
        value = 0.0
        for p in self.points:
            if (p.ts - start) / length <= fraction:
                value = p.used
            else:
                break
        return value


def episodes(points: list[Point], length: timedelta) -> list[Episode]:
    out: list[Episode] = []
    current: Episode | None = None
    for p in sorted(points, key=lambda p: p.ts):
        prev = current.points[-1] if current and current.points else None
        new = current is None or prev is None
        if not new:
            reset = current.resets_at
            if (
                p.resets_at is not None
                and reset is not None
                and abs(p.resets_at - reset) > RESET_JITTER
                or p.used < prev.used - DROP
                or p.ts - prev.ts >= length
                or p.ts >= current.end(length)
            ):
                new = True
        if new:
            current = Episode()
            out.append(current)
        current.points.append(p)
    return out


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def forecast_series(
    key: str, scope: str, points: list[Point], now: datetime
) -> dict | None:
    """Pace and band for the current window of one series, or None if it has none."""
    length = window_length(key)
    if length is None or not points:
        return None
    eps = episodes([p for p in points if p.ts <= now], length)
    if not eps:
        return None
    current = eps[-1]
    if now >= current.end(length):
        return None  # the last window is over; no reading since
    start = current.start(length)
    elapsed = min(1.0, max(0.0, (now - start) / length))
    used = current.points[-1].used
    growth = [
        max(0.0, e.peak - e.used_at(elapsed, length))
        for e in eps[:-1]
        if len(e.points) >= 2
    ]
    band = None
    if len(growth) >= MIN_HISTORY:
        projected = [used + g for g in growth]
        # A rolling window stops at 100 %; only a spend limit can go past it.
        cap = float("inf") if key == "spend_limit" else 100.0
        band = {
            q: min(cap, max(used, _percentile(projected, p)))
            for q, p in (("p10", 0.1), ("p50", 0.5), ("p90", 0.9))
        }
    return {
        "key": key,
        "scope": scope,
        "label": label_for(key),
        "used": used,
        "elapsed_pct": round(elapsed * 100, 1),
        "pace_delta": round(used - elapsed * 100, 1),
        "episode_start": start.isoformat(),
        "resets_at": current.end(length).isoformat(),
        "band": band,
        "history_windows": len(growth),
        "note": None
        if band
        else (f"not enough history: {len(growth)} of {MIN_HISTORY} past windows"),
    }


def forecast(conn, now: datetime | None = None) -> list[dict]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    from ..quota import CLAUDE_SAMPLES, canonical_scope

    canon = canonical_scope(conn)
    series: dict[tuple[str, str], list[Point]] = {}
    for r in conn.execute(
        "SELECT ts, key, scope, utilization, resets_at FROM quota_samples"
        f" WHERE {CLAUDE_SAMPLES} ORDER BY ts"
    ):
        stamp = _parse_time(r["ts"])
        if stamp is None:
            continue
        series.setdefault((r["key"], canon(r["scope"])), []).append(
            Point(stamp, float(r["utilization"]), _parse_time(r["resets_at"]))
        )
    out = []
    for (key, scope), points in sorted(series.items()):
        result = forecast_series(key, scope, points, now)
        if result:
            out.append(result)
    return out
