"""The one filter parser every endpoint shares, so `from/to/source/project/model`
mean exactly the same thing no matter which route reads them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fastapi import HTTPException, Query

# Every endpoint accepts the same range. Outside it, boundary arithmetic in a
# far-east zone overflowed and returned 500 (A-028).
EARLIEST = date(1970, 1, 1)
LATEST = date(9998, 12, 31)


@dataclass
class Filters:
    date_from: date | None
    date_to: date | None
    source: str
    project: str | None
    model: str | None
    metric: str


def _parse_date(value: str | None, param: str) -> date | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"{param} expects YYYY-MM-DD, got {value!r}") from None
    if not EARLIEST <= parsed <= LATEST:
        raise HTTPException(400, f"{param} must be between {EARLIEST} and {LATEST}")
    return parsed


def get_filters(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    source: Literal[
        "all", "claude", "claude-code", "cowork", "codex", "copilot", "web"
    ] = Query("all"),
    project: str | None = Query(None, max_length=500),
    model: str | None = Query(None, max_length=200),
    metric: Literal["cost", "tokens"] = Query("cost"),
) -> Filters:
    """Validated once here, identically for every endpoint (A-073)."""
    start, end = _parse_date(from_, "from"), _parse_date(to, "to")
    if start and end and start > end:
        raise HTTPException(400, "from must not be after to")
    return Filters(
        date_from=start,
        date_to=end,
        source=source,
        project=project,
        model=model,
        metric=metric,
    )
