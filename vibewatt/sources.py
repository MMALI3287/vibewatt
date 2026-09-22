"""Normalization of local session logs. Discovery lives in vibewatt.ingest.

Two producers write the same broad JSONL shape in different places:

  Claude Code   ~/.claude/projects/**/*.jsonl
  Cowork        <desktop data dir>/{local-agent-mode-sessions,claude-code-sessions}/**/audit.jsonl

Cowork renames three fields (session_id, _audit_timestamp, _audit_hmac) but keeps
the message/usage payload identical, so normalizing the envelope is enough.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

CLAUDE_CODE = "claude-code"
COWORK = "cowork"

@dataclass(frozen=True)
class Turn:
    """One billable assistant response."""

    source: str
    ts: datetime
    model: str
    input: int
    cache_5m: int
    cache_1h: int
    cache_read: int
    output: int
    thinking: int
    web_searches: int
    fast: bool
    geo: str | None
    sidechain: bool
    project: str
    session: str
    key: tuple[str, str]
    version: str | None = None


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _project_name(source: str, path: Path, cwd: str | None) -> str:
    if cwd:
        return Path(cwd).name or cwd
    if source == COWORK:
        # .../<account>/<space>/<local_id>/audit.jsonl
        return path.parent.parent.name or "cowork"
    return path.parent.name


def read_file(source: str, path: Path) -> Iterator[Turn]:
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue

            ts = _parse_ts(rec.get("timestamp") or rec.get("_audit_timestamp"))
            model = msg.get("model")
            if ts is None or not model or model == "<synthetic>":
                continue

            # Cache writes are billed at two different rates. Fall back to the
            # flat total only when the per-TTL split is absent.
            split = usage.get("cache_creation") or {}
            total_write = int(usage.get("cache_creation_input_tokens") or 0)
            if isinstance(split, dict) and split:
                c5m = int(split.get("ephemeral_5m_input_tokens") or 0)
                c1h = int(split.get("ephemeral_1h_input_tokens") or 0)
                if c5m + c1h == 0 and total_write:
                    c5m = total_write
            else:
                c5m, c1h = total_write, 0

            details = usage.get("output_tokens_details") or {}
            server = usage.get("server_tool_use") or {}
            geo = usage.get("inference_geo")

            session = str(rec.get("sessionId") or rec.get("session_id") or path.stem)
            yield Turn(
                source=source,
                ts=ts,
                model=model,
                input=int(usage.get("input_tokens") or 0),
                cache_5m=c5m,
                cache_1h=c1h,
                cache_read=int(usage.get("cache_read_input_tokens") or 0),
                output=int(usage.get("output_tokens") or 0),
                thinking=int((details or {}).get("thinking_tokens") or 0),
                web_searches=int((server or {}).get("web_search_requests") or 0),
                fast=usage.get("speed") == "fast",
                geo=geo if geo in ("us", "global") else None,
                sidechain=bool(rec.get("isSidechain")),
                project=_project_name(source, path, rec.get("cwd")),
                session=session,
                key=response_key(
                    str(msg.get("id") or ""),
                    # Cowork's audit.jsonl spells it request_id.
                    str(rec.get("requestId") or rec.get("request_id") or ""),
                    session,
                    ts,
                ),
                version=str(rec["version"]) if rec.get("version") else None,
            )


def response_key(msg_id: str, request_id: str, session: str, ts: datetime) -> tuple[str, str]:
    """Identity of one API response, shared by every line that repeats it.

    Claude Code writes one line per content block and repeats the response's
    usage on each, so (message id, request id) is the identity. Without a
    request id, LLM gateways reuse message ids across calls, so the session and
    timestamp are added. Without a message id there is nothing to dedup on.
    """
    if msg_id and request_id:
        return msg_id, request_id
    if msg_id:
        return msg_id, f"~{session}|{ts.isoformat()}"
    return f"{session}:{ts.isoformat()}", ""


TOKEN_FIELDS = ("input", "cache_5m", "cache_1h", "cache_read", "output", "thinking", "web_searches")


def _attribution(turn: Turn) -> tuple:
    # Main-thread lines own a response over sidechain replays; after that the
    # earliest line wins, so the result never depends on which file came first.
    return (turn.sidechain, turn.ts, turn.session, turn.project, turn.source)


def merge(a: Turn, b: Turn) -> Turn:
    """Two lines of the same response. Keep the per-field maximum.

    The first line of a streamed response is a placeholder (output 1-3 tokens);
    the final line carries the real counts. Every field only grows while a
    response streams, so the maximum is the final value whatever the order.
    """
    keep = a if _attribution(a) <= _attribution(b) else b
    return replace(
        keep,
        **{f: max(getattr(a, f), getattr(b, f)) for f in TOKEN_FIELDS},
        fast=a.fast or b.fast,
        geo=a.geo or b.geo,
        version=max(a.version or "", b.version or "") or None,
    )


def dedupe(turns) -> tuple[list[Turn], int]:
    """Collapse repeated lines into one Turn per response. Returns (turns, dropped).

    A sidechain line whose message id also appears on a main-thread line is a
    replay (``/btw`` asides copy parent messages under a new request id) and is
    dropped.
    """
    by_key: dict[tuple[str, str], Turn] = {}
    lines = 0
    for turn in turns:
        lines += 1
        seen = by_key.get(turn.key)
        by_key[turn.key] = merge(seen, turn) if seen else turn
    main_ids = {t.key[0] for t in by_key.values() if not t.sidechain}
    kept = [t for t in by_key.values() if not (t.sidechain and t.key[0] in main_ids)]
    return kept, lines - len(kept)


def load(files: list[tuple[str, Path]]) -> tuple[list[Turn], int]:
    """Read and dedupe every file. Returns (turns, lines collapsed)."""
    return dedupe(turn for source, path in files for turn in read_file(source, path))


def read_prompts(files: list[tuple[str, Path]]) -> list[dict]:
    """Recover the prompt text Claude Code records per session.

    Claude Code writes a `last-prompt` record carrying the user's prompt. That
    is the honest source for "what you worked on": it needs no API call, no
    OAuth and no model, and it is already on disk. Falls back to the session's
    first user message when no such record exists.
    """
    found: list[dict] = []
    for source, path in files:
        first_user: dict | None = None
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line or '"last-prompt"' not in line and '"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = rec.get("type")
                session = str(rec.get("sessionId") or rec.get("session_id") or path.stem)
                if kind == "last-prompt":
                    text = (rec.get("lastPrompt") or "").strip()
                    if text:
                        found.append({"session": session, "ts": rec.get("timestamp"),
                                      "text": text[:500]})
                elif kind == "user" and first_user is None:
                    msg = rec.get("message") or {}
                    content = msg.get("content")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for blk in content:
                            if isinstance(blk, dict) and blk.get("type") == "text":
                                text = blk.get("text", "")
                                break
                    text = text.strip()
                    if text and not text.startswith("<"):
                        first_user = {"session": session,
                                      "ts": rec.get("timestamp"), "text": text[:500]}
        if first_user and not any(f["session"] == first_user["session"] for f in found):
            found.append(first_user)
    return found
