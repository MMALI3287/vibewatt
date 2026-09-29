"""Rankable advice grounded in findings and explicitly labelled heuristics."""

from __future__ import annotations

from datetime import datetime

from ..pricing import MILLION, normalize, rate_for
from .models import Finding


def detect(
    session: str, rows: list[dict], evidence: list[Finding], overrides: dict | None
) -> list[Finding]:
    tips = []
    for finding in evidence:
        if finding.rule not in {"low_cache_hit", "subagent_heavy"}:
            continue
        tips.append(
            Finding(
                "tip",
                f"tip_{finding.rule}",
                "info",
                session,
                finding.day,
                "Improve cache reuse"
                if finding.rule == "low_cache_hit"
                else "Review subagent overhead",
                "Keep stable instructions together and check cache configuration."
                if finding.rule == "low_cache_hit"
                else "Try narrower subagent tasks with only the context they need. Compare results before changing the workflow.",
                session_id=session,
                savings_usd=finding.savings_usd,
                metrics={"evidence_rule": finding.rule},
            )
        )
    opus = [r for r in rows if "opus" in (normalize(r["model"]) or "")]
    if len(opus) >= 5 and sum(r["output"] for r in opus) / len(opus) < 500:
        tips.append(
            Finding(
                "tip",
                "model_mix",
                "info",
                session,
                rows[-1]["day"],
                "Evaluate a cheaper model",
                "Five or more Opus responses average fewer than 500 output tokens. Try Sonnet "
                "on representative tasks and compare quality. Short output alone cannot show "
                "that a cheaper model is sufficient.",
                session_id=session,
                metrics={
                    "opus_responses": len(opus),
                    "mean_output": sum(r["output"] for r in opus) / len(opus),
                },
            )
        )
    fast = [r for r in rows if r["fast"]]
    if fast:
        premium = 0.0
        known = True
        for row in fast:
            billed = rate_for(
                row["model"],
                ts=datetime.fromisoformat(row["ts"]),
                prompt_tokens=row["input"]
                + row["cache_read"]
                + row["cache_5m"]
                + row["cache_1h"],
                fast=True,
                geo=row["geo"],
                overrides=overrides,
            )
            standard = rate_for(
                row["model"],
                ts=datetime.fromisoformat(row["ts"]),
                prompt_tokens=row["input"]
                + row["cache_read"]
                + row["cache_5m"]
                + row["cache_1h"],
                geo=row["geo"],
                overrides=overrides,
            )
            if billed is None or standard is None:
                known = False
                continue
            quantities = (
                row["input"],
                row["cache_5m"],
                row["cache_1h"],
                row["cache_read"],
                row["output"],
            )
            premium += (
                sum(n * max(0, a - b) for n, a, b in zip(quantities, billed, standard))
                / MILLION
            )
        if premium > 0 or not known:
            tips.append(
                Finding(
                    "tip",
                    "fast_mode",
                    "info",
                    session,
                    rows[-1]["day"],
                    "Review fast-mode spend",
                    "Use standard speed where latency is less important. The estimate compares "
                    "the same tokens at fast and standard rates, including cache TTL and geography.",
                    session_id=session,
                    savings_usd=premium if known else None,
                    metrics={
                        "fast_responses": len(fast),
                        "pricing": "known" if known else "unpriced",
                    },
                )
            )
    return tips
