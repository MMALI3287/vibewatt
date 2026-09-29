"""Cache opportunity estimates use each response's actual billing rate."""

from __future__ import annotations

from datetime import datetime

from ..pricing import MILLION, rate_for
from .models import Finding, input_tokens


def detect(session: str, rows: list[dict], overrides: dict | None) -> list[Finding]:
    served = sum(input_tokens(r) for r in rows)
    reads = sum(r["cache_read"] for r in rows)
    if served <= 200_000 or reads / served >= 0.5:
        return []
    saving = 0.0
    unpriced = False
    for row in rows:
        rate = rate_for(
            row["model"],
            ts=datetime.fromisoformat(row["ts"]),
            prompt_tokens=row["input"]
            + row["cache_read"]
            + row["cache_5m"]
            + row["cache_1h"],
            fast=bool(row["fast"]),
            geo=row["geo"],
            overrides=overrides,
        )
        if rate is None:
            unpriced = True
            continue
        saving += (
            row["input"] * max(0, rate.input - rate.cache_read)
            + row["cache_5m"] * max(0, rate.cache_5m - rate.cache_read)
            + row["cache_1h"] * max(0, rate.cache_1h - rate.cache_read)
        ) / MILLION
    return [
        Finding(
            "cache",
            "low_cache_hit",
            "warning",
            session,
            rows[-1]["day"],
            "Low prompt-cache hit rate",
            "Less than 50% of input was served from cache across more than 200k input tokens. "
            "The saving is an upper-bound estimate if uncached input and cache writes could "
            "instead be cache reads. It does not establish that the content is reusable.",
            session_id=session,
            savings_usd=None if unpriced else saving,
            metrics={
                "input_tokens": served,
                "hit_rate": reads / served,
                "pricing": "unpriced" if unpriced else "known",
            },
        )
    ]
