"""Normalization of local session logs. Discovery lives in vibewatt.ingest.

Two producers write the same broad JSONL shape in different places:

  Claude Code   ~/.claude/projects/**/*.jsonl
  Cowork        <desktop data dir>/{local-agent-mode-sessions,claude-code-sessions}/**/audit.jsonl

Cowork renames three fields (session_id, _audit_timestamp, _audit_hmac) but keeps
the message/usage payload identical, so normalizing the envelope is enough.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

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
    # projects/<encoded project>/<session>/subagents/agent.jsonl belongs to the
    # project, not to a folder called "subagents" (A-065).
    parts = path.parts
    if "projects" in parts:
        index = len(parts) - 1 - parts[::-1].index("projects")
        if index + 2 < len(parts):
            return parts[index + 1]
    return path.parent.name


def _int(value) -> int:
    # bool is an int subclass; a true/false token count is a malformed record.
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"not a token count: {value!r}")
    return int(value)


def _turn(source: str, path: Path, rec: dict, cwd: str | None) -> Turn | str:
    """One assistant record as a Turn, or the reason it is skipped."""
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return "no_message"
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return "no_usage"
    ts = _parse_ts(rec.get("timestamp") or rec.get("_audit_timestamp"))
    if ts is None:
        return "bad_timestamp"
    model = msg.get("model")
    if model == "<synthetic>":
        return "synthetic"
    if not isinstance(model, str) or not model:
        return "no_model"

    # Cache writes are billed at two TTL rates. A record with only the flat
    # total is assumed to be 5 m, the API default when no TTL is requested.
    # A split that sums to less than the total puts the remainder on 5 m too,
    # so no written token is dropped (A-063).
    split = usage.get("cache_creation")
    total_write = _int(usage.get("cache_creation_input_tokens"))
    c5m = c1h = 0
    if isinstance(split, dict):
        c5m = _int(split.get("ephemeral_5m_input_tokens"))
        c1h = _int(split.get("ephemeral_1h_input_tokens"))
    c5m += max(0, total_write - c5m - c1h)

    details = usage.get("output_tokens_details")
    server = usage.get("server_tool_use")
    geo = usage.get("inference_geo")
    session = str(rec.get("sessionId") or rec.get("session_id") or path.stem)
    return Turn(
        source=source,
        ts=ts,
        model=model,
        input=_int(usage.get("input_tokens")),
        cache_5m=c5m,
        cache_1h=c1h,
        cache_read=_int(usage.get("cache_read_input_tokens")),
        output=_int(usage.get("output_tokens")),
        thinking=_int(details.get("thinking_tokens")) if isinstance(details, dict) else 0,
        web_searches=(_int(server.get("web_search_requests"))
                      if isinstance(server, dict) else 0),
        fast=usage.get("speed") == "fast",
        # Only "us" changes the price (1.1x). "global", "not_available" and any
        # other value bill at the standard rate.
        geo="us" if geo == "us" else None,
        sidechain=bool(rec.get("isSidechain")),
        project=_project_name(source, path, cwd),
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


def _raw_tokens(rec: dict) -> int:
    usage = (rec.get("message") or {}).get("usage") if isinstance(rec.get("message"), dict) else None
    if not isinstance(usage, dict):
        return 0
    total = 0
    for field in ("input_tokens", "output_tokens"):
        value = usage.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value)
    return total


def stats_line(rec: dict, path: Path) -> tuple[datetime, str, int, int] | None:
    """How Claude's own Stats count one record: (time, session, message, tokens).

    Reproduced exactly against the desktop app (33 sessions, 21,183 messages,
    14,964,413 tokens at 2026-09-15 19:13:45 JST): messages are user and
    assistant lines outside subagents; tokens are the naive input + output of
    every assistant line, subagents included, with no dedup and no cache.
    """
    kind = rec.get("type")
    if kind not in ("user", "assistant"):
        return None
    stamp = _parse_ts(rec.get("timestamp") or rec.get("_audit_timestamp"))
    if stamp is None:
        return None
    session = str(rec.get("sessionId") or rec.get("session_id") or path.stem)
    message = 0 if rec.get("isSidechain") else 1
    tokens = _raw_tokens(rec) if kind == "assistant" else 0
    return stamp, session, message, tokens


def read_file(source: str, path: Path, drops: Counter | None = None,
              raw: dict | None = None) -> Iterator[Turn]:
    """Every billable response line in one file, not deduped.

    Raises OSError when the file cannot be opened, so a sync does not record a
    locked file as read (A-024). A malformed record is skipped and counted in
    `drops` by reason; it never aborts the file (A-023).

    `raw`, when given, collects per (UTC hour, session) the counts Claude's own
    Stats use (see stats_line). They do not dedupe, so they are only ever shown
    as a labelled comparison, never as a headline (A-116, A-125).
    """
    drops = drops if drops is not None else Counter()
    # utf-8-sig drops a leading BOM, which otherwise hides the first record (A-069).
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        cwd: str | None = None
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
            # Carry the last cwd forward: some records omit it (A-065).
            if isinstance(rec.get("cwd"), str) and rec["cwd"]:
                cwd = rec["cwd"]
            if raw is not None:
                line_stats = stats_line(rec, path)
                if line_stats is not None:
                    stamp, session, message, tokens = line_stats
                    entry = raw.setdefault((stamp.isoformat()[:13], session), [0, 0])
                    entry[0] += message
                    entry[1] += tokens
            if rec.get("type") != "assistant":
                continue
            try:
                turn = _turn(source, path, rec, cwd)
            except (TypeError, ValueError, AttributeError, OverflowError):
                drops["bad_field"] += 1
                continue
            if isinstance(turn, str):
                if turn != "synthetic":  # a local stand-in, not a malformed record
                    drops[turn] += 1
                continue
            yield turn


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
    """Read and dedupe every readable file. Returns (turns, lines collapsed)."""

    def lines():
        for source, path in files:
            try:
                yield from read_file(source, path)
            except OSError:
                continue

    return dedupe(lines())


# Title sources, best first. Claude Code writes custom-title when the user
# names a session and ai-title when it names one itself; `summary` records no
# longer exist (A-059).
TITLE_RANK = {"custom-title": 4, "ai-title": 3, "last-prompt": 2, "first-user": 1}
_TITLE_FIELD = {"custom-title": "customTitle", "ai-title": "aiTitle",
                "last-prompt": "lastPrompt"}


def _first_user_text(rec: dict) -> str:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                return str(blk.get("text", ""))
    return ""


def read_titles(files: list[tuple[str, Path]]) -> list[dict]:
    """One title per session: the best kind seen, the latest of that kind.

    Only the title is kept. Earlier prompts are not retained (A-067).
    """
    best: dict[str, dict] = {}
    for source, path in files:
        try:
            handle = path.open("r", encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"type"' not in line or not any(
                    k in line for k in ('"custom-title"', '"ai-title"', '"last-prompt"', '"user"')
                ):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                kind = rec.get("type")
                if kind == "user":
                    kind = "first-user"
                if kind not in TITLE_RANK:
                    continue
                session = str(rec.get("sessionId") or rec.get("session_id") or path.stem)
                seen = best.get(session)
                if kind == "first-user":
                    if seen is not None:
                        continue
                    text = _first_user_text(rec).strip()
                    if text.startswith("<"):  # command and hook wrappers, not prose
                        continue
                else:
                    raw = rec.get(_TITLE_FIELD[kind])
                    text = raw.strip() if isinstance(raw, str) else ""
                if not text:
                    continue
                if seen is None or TITLE_RANK[kind] >= TITLE_RANK[seen["kind"]]:
                    best[session] = {"session": session, "kind": kind,
                                     "rank": TITLE_RANK[kind], "ts": rec.get("timestamp"),
                                     "text": text[:500]}
    return list(best.values())
