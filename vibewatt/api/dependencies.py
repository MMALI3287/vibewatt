"""The one filter parser every endpoint shares, so `from/to/source/project/model`
mean exactly the same thing no matter which route reads them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException, Query


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
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"{param} expects YYYY-MM-DD, got {value!r}")


def get_filters(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    source: str = Query("all"),
    project: str | None = Query(None),
    model: str | None = Query(None),
    metric: str = Query("cost"),
) -> Filters:
    return Filters(
        date_from=_parse_date(from_, "from"),
        date_to=_parse_date(to, "to"),
        source=source,
        project=project,
        model=model,
        metric=metric,
    )
