"""Discovery and normalization of local session logs.

Two producers write the same broad JSONL shape in different places:

  Claude Code   ~/.claude/projects/**/*.jsonl
  Cowork        <desktop data dir>/{local-agent-mode-sessions,claude-code-sessions}/**/audit.jsonl

Cowork renames three fields (session_id, _audit_timestamp, _audit_hmac) but keeps
the message/usage payload identical, so normalizing the envelope is enough.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

CLAUDE_CODE = "claude-code"
COWORK = "cowork"

_COWORK_DIRS = ("local-agent-mode-sessions", "claude-code-sessions")


def _desktop_data_dirs() -> list[Path]:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        roots = [home / "Library" / "Application Support" / "Claude"]
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        roots = [Path(appdata) / "Claude"] if appdata else []
    else:
        cfg = os.environ.get("XDG_CONFIG_HOME") or (home / ".config")
        roots = [Path(cfg) / "Claude"]
    extra = os.environ.get("CCBURN_COWORK_DIR")
    if extra:
        roots = [Path(p).expanduser() for p in extra.split(os.pathsep)] + roots
    return roots


def claude_code_roots() -> list[Path]:
    """Honour CLAUDE_CONFIG_DIR, which may hold several colon-separated paths."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    roots = (
        [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
        if configured
        else [Path.home() / ".claude"]
    )
    return [r / "projects" for r in roots]


def discover(cfg: dict | None = None) -> list[tuple[str, Path]]:
    """Return (source, file) pairs for every session log found on this machine.

    Claude Code on the web and Cowork remote sessions are deliberately absent:
    they run in throwaway cloud containers and never write to this disk. See
    ccburn.quota for the account-level figures that do include them.
    """
    cfg = cfg or {}
    found: list[tuple[str, Path]] = []
    for root in claude_code_roots():
        if root.is_dir():
            found += [(CLAUDE_CODE, p) for p in sorted(root.rglob("*.jsonl"))]
    for base in _desktop_data_dirs():
        for name in _COWORK_DIRS:
            root = base / name
            if root.is_dir():
                found += [(COWORK, p) for p in sorted(root.rglob("audit.jsonl"))]
    return found


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
                session=str(rec.get("sessionId") or rec.get("session_id") or path.stem),
                # A response is identified by its message id plus the request that
                # produced it; the same turn is replayed into several files.
                key=(str(msg.get("id") or ""), str(rec.get("requestId") or "")),
            )


def load(files: list[tuple[str, Path]]) -> tuple[list[Turn], int]:
    """Read every file, dropping turns already seen. Returns (turns, duplicates)."""
    seen: set[tuple[str, str]] = set()
    turns: list[Turn] = []
    duplicates = 0
    for source, path in files:
        for turn in read_file(source, path):
            if turn.key != ("", "") and turn.key in seen:
                duplicates += 1
                continue
            if turn.key != ("", ""):
                seen.add(turn.key)
            turns.append(turn)
    return turns, duplicates


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
