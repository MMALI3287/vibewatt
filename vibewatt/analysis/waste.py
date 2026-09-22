"""Content-free signals for investigation, not proof that work was wasted."""

from __future__ import annotations

from collections import Counter

from .models import Finding, timestamp, total_tokens


def detect(session: str, rows: list[dict], reads: list[dict]) -> list[Finding]:
    findings = []
    day = rows[-1]["day"] if rows else (reads[-1]["day"] if reads else None)

    def add(rule: str, title: str, detail: str, metrics: dict) -> None:
        findings.append(
            Finding(
                "waste",
                rule,
                "info",
                session,
                day,
                title,
                detail,
                session_id=session,
                metrics=metrics,
            )
        )

    paths = Counter(r["path_hash"] for r in reads)
    repeated = [count for count in paths.values() if count >= 3]
    if repeated:
        add(
            "repeated_reads",
            "Repeated file reads",
            "At least one file was requested by Read three or more times. Distinct tool IDs "
            "are counted once across transcript replays. Reads may cover different ranges "
            "or changed content, so inspect the session before changing your workflow.",
            {
                "files": len(repeated),
                "read_calls": sum(repeated),
                "max_reads": max(repeated),
            },
        )
    if not rows:
        return findings
    output = sum(r["output"] for r in rows)
    cache = sum(r["cache_read"] for r in rows)
    if cache >= 200_000 and cache > 100 * output:
        add(
            "cache_to_output",
            "High cache-read to output ratio",
            "At least 200k cache-read tokens and more than 100 cache-read tokens per output "
            "token. This can be legitimate context-heavy work.",
            {"cache_read": cache, "output": output},
        )
    tokens = sum(total_tokens(r) for r in rows)
    sidechain = sum(total_tokens(r) for r in rows if r["sidechain"])
    if len(rows) >= 5 and tokens and sidechain / tokens >= 0.5:
        add(
            "subagent_heavy",
            "Subagent-heavy session",
            "At least half of the tokens in five or more responses belong to subagents. "
            "Check whether repeated context could be reduced.",
            {"subagent_share": sidechain / tokens, "responses": len(rows)},
        )
    first, last = timestamp(rows[0]["ts"]), timestamp(rows[-1]["ts"])
    hours = (last - first).total_seconds() / 3600 if first and last else 0
    if len(rows) >= 5 and hours >= 2 and output < 1000:
        add(
            "long_low_output",
            "Long session with little output",
            "Five or more responses span at least two hours with fewer than 1,000 output "
            "tokens. Elapsed time includes idle gaps and is not active work time.",
            {"elapsed_hours": hours, "output": output, "responses": len(rows)},
        )
    return findings
