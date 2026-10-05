"""Codex CLI and desktop: ~/.codex/{sessions,archived_sessions}/**/rollout-*.jsonl

A rollout is append-only. Billable usage arrives as `event_msg` records of type
`token_count`, each carrying a running session total and the usage of the call
that just finished (`last_token_usage`). Only the per-call figure is a response;
the running total is never summed. See docs/DATA-SOURCES.md, "Codex".
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from vibewatt.sources import CODEX, Turn, _int, _parse_ts

_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def roots() -> list[Path]:
    return [home() / "sessions", home() / "archived_sessions"]


# Window lengths Codex reports, as the keys vibewatt stores them under.
_WINDOWS = {
    300: ("codex_five_hour", "Codex 5-hour"),
    10080: ("codex_seven_day", "Codex weekly"),
}


def _reset_time(value) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def plan_readings(path: Path, scope: str) -> list[tuple]:
    """Codex plan-limit readings in one rollout, as quota_samples rows.

    Every `token_count` event repeats the current `rate_limits`, so only a change
    per window is kept. `scope` names the ChatGPT account ("chatgpt:<uuid>"),
    which keeps these rows apart from Anthropic plan samples. Raises OSError on
    an unreadable file.
    """
    rows: list[tuple] = []
    last: dict[str, tuple] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            if '"rate_limits"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = rec.get("payload") if isinstance(rec, dict) else None
            limits = payload.get("rate_limits") if isinstance(payload, dict) else None
            if not isinstance(limits, dict):
                continue
            stamp = _parse_ts(rec.get("timestamp"))
            if stamp is None:
                continue
            plan = limits.get("plan_type")
            plan = plan if isinstance(plan, str) and plan else "unknown plan"
            for name in ("primary", "secondary"):
                window = limits.get(name)
                if not isinstance(window, dict):
                    continue
                minutes = window.get("window_minutes")
                used = window.get("used_percent")
                if isinstance(minutes, bool) or not isinstance(minutes, int):
                    continue
                if isinstance(used, bool) or not isinstance(used, (int, float)):
                    continue
                key, label = _WINDOWS.get(
                    minutes, (f"codex_{minutes}m", f"Codex {minutes}-minute")
                )
                resets_at = _reset_time(window.get("resets_at"))
                reading = (float(used), resets_at, plan)
                if last.get(key) == reading:
                    continue
                last[key] = reading
                rows.append(
                    (
                        stamp.isoformat(),
                        key,
                        f"{label} ({plan})",
                        scope,
                        float(used),
                        resets_at,
                        "codex-log",
                    )
                )
    return rows


def discover(cfg: dict | None = None) -> list[Path]:
    from . import walk

    files: list[Path] = []
    for root in roots():
        if root.is_dir():
            files.extend(walk(root, "rollout-*.jsonl"))
    return files


def _usage(info: dict, name: str) -> tuple[int, ...] | None:
    block = info.get(name)
    if not isinstance(block, dict):
        return None
    return tuple(_int(block.get(field)) for field in _FIELDS)


def parse(
    path: Path, drops: Counter | None = None, raw: dict | None = None
) -> Iterator[Turn]:
    """One Turn per `token_count` event with usage, not deduped.

    The same event is written more than once (rate-limit refreshes repeat the
    last total), so identity is the session plus the full running total: two
    events with the same total are one response, and a later call always moves
    at least one field. Raises OSError on an unreadable file so the sync retries
    it. A malformed record is counted in `drops` and never aborts the file.
    """
    drops = drops if drops is not None else Counter()
    session = path.stem
    cwd: str | None = None
    version: str | None = None
    model: str | None = None
    seen_meta = False
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                drops["bad_json"] += 1
                continue
            if not isinstance(rec, dict):
                drops["not_an_object"] += 1
                continue
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = rec.get("type")
            if kind == "session_meta" and not seen_meta:
                # A forked rollout can replay its parent's meta later in the
                # file; the first one is this session's own.
                seen_meta = True
                if isinstance(payload.get("id"), str) and payload["id"]:
                    session = payload["id"]
                if isinstance(payload.get("cwd"), str) and payload["cwd"]:
                    cwd = payload["cwd"]
                if isinstance(payload.get("cli_version"), str):
                    version = payload["cli_version"]
                continue
            if kind == "turn_context":
                if isinstance(payload.get("model"), str) and payload["model"]:
                    model = payload["model"]
                if isinstance(payload.get("cwd"), str) and payload["cwd"]:
                    cwd = payload["cwd"]
                continue
            if kind != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue  # a rate-limit-only refresh carries no usage
            try:
                last = _usage(info, "last_token_usage")
                total = _usage(info, "total_token_usage")
            except (TypeError, ValueError, OverflowError):
                drops["bad_field"] += 1
                continue
            stamp = _parse_ts(rec.get("timestamp"))
            if last is None or total is None:
                drops["no_usage"] += 1
                continue
            # total_tokens is ignored: it also counts a baseline inherited from
            # a parent thread, which has no breakdown and no price.
            if not any(last):
                drops["no_usage"] += 1
                continue
            if stamp is None:
                drops["bad_timestamp"] += 1
                continue
            if model is None:
                drops["no_model"] += 1
                continue
            inp, cached, written, out, reasoning = last
            yield Turn(
                source=CODEX,
                ts=stamp,
                model=model,
                # OpenAI counts cached tokens inside input_tokens.
                input=max(0, inp - cached),
                cache_5m=written,
                cache_1h=0,
                cache_read=cached,
                output=out,
                thinking=reasoning,
                web_searches=0,
                fast=False,
                geo=None,
                sidechain=False,
                project=Path(cwd).name or cwd if cwd else path.parent.name,
                session=session,
                key=(f"codex:{session}", "|".join(map(str, total))),
                version=version,
            )
