"""Serializable evidence shared by the analysis rules and API."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, tzinfo
from typing import Literal

Kind = Literal["anomaly", "cache", "tip", "waste", "peak", "context"]
Severity = Literal["info", "warning", "urgent"]


@dataclass
class Finding:
    kind: Kind
    rule: str
    severity: Severity
    subject: str
    day: str | None
    title: str
    detail: str
    coverage: Literal["local", "harvested", "account"] = "local"
    session_id: str | None = None
    savings_usd: float | None = None
    metrics: dict[str, float | str | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def timestamp(value: str | None) -> datetime | None:
    try:
        stamp = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def local_day(value: str | None, tz: tzinfo) -> str | None:
    stamp = timestamp(value)
    return stamp.astimezone(tz).date().isoformat() if stamp else None


def input_tokens(row: dict) -> int:
    return row["input"] + row["cache_5m"] + row["cache_1h"] + row["cache_read"]


def total_tokens(row: dict) -> int:
    return input_tokens(row) + row["output"]
