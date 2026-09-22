"""Shared helpers for d1 probes. Not a test module."""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))


def rec(
    msg_id="m1",
    req="r1",
    ts="2026-09-15T20:00:00Z",
    session="s1",
    cwd="/work/demo",
    model="claude-opus-5",
    inp=10,
    out=5,
    usage_extra=None,
    **envelope,
):
    usage = {
        "input_tokens": inp,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": out,
    }
    if usage_extra:
        usage.update(usage_extra)
    message = {"model": model, "usage": usage}
    if msg_id is not None:
        message["id"] = msg_id
    r = {"type": "assistant", "timestamp": ts, "sessionId": session, "message": message}
    if cwd is not None:
        r["cwd"] = cwd
    if req is not None:
        r["requestId"] = req
    r.update(envelope)
    return r


def write_jsonl(path: Path, records, trailing=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r) + "\n" for r in records) + trailing
    path.write_text(text, encoding="utf-8")
    return path


def projects_root(tmp_path: Path) -> Path:
    return tmp_path / "claude" / "projects"
