"""Diagnostics: show exactly what ccburn can and cannot see.

Every number in a usage report is only as good as the files behind it. When a
figure looks wrong the useful question is not "is the maths right" but "which
days actually reached the disk". This prints that, so a surprising streak or a
missing project can be explained instead of guessed at.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import quota
from .config import data_dir, user_config_dir
from .sources import CLAUDE_CODE, COWORK, claude_code_roots, discover, load


def _claude_settings() -> tuple[Path | None, dict]:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(configured.split(os.pathsep)[0]).expanduser() if configured else Path.home() / ".claude"
    path = base / "settings.json"
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (path if path.exists() else None), {}


def run(cfg: dict, tz) -> int:
    today = datetime.now(tz).date()
    out = sys.stdout
    say = lambda s="": print(s, file=out)

    say()
    say("  ccburn doctor")
    say()
    say(f"  platform        {platform.system()} {platform.release()}  python {platform.python_version()}")
    say(f"  timezone        {cfg.get('timezone')}  ->  today is {today}")
    say(f"  system date     {date.today()}  (differs from above if your tz setting is not local)")
    say(f"  config dir      {user_config_dir()}")
    say(f"  data dir        {data_dir()}")

    # --- retention, the thing that silently destroys history -----------------
    say()
    say("  retention")
    path, settings = _claude_settings()
    period = settings.get("cleanupPeriodDays")
    if path is None:
        say("    no Claude Code settings.json found; the 30-day default applies")
        say("    ACTION: set cleanupPeriodDays to 3650, logs older than 30 days are being deleted")
    elif period is None:
        say(f"    {path}")
        say("    cleanupPeriodDays not set -> default 30 days")
        say("    ACTION: set cleanupPeriodDays to 3650. Cleanup runs at every startup.")
    elif period == 0:
        say(f"    cleanupPeriodDays = 0  <-- THIS DISABLES TRANSCRIPT WRITING ENTIRELY")
        say("    ACTION: change it to 3650. Zero does not mean keep forever.")
    elif period < 365:
        say(f"    cleanupPeriodDays = {period} -> logs older than {period} days are deleted at startup")
        say("    ACTION: raise it to 3650 if you want full history")
    else:
        say(f"    cleanupPeriodDays = {period}  (good)")

    # --- what is actually on disk -------------------------------------------
    say()
    say("  sources")
    files = discover(cfg)
    by_source = defaultdict(list)
    for source, fp in files:
        by_source[source].append(fp)
    for label, roots in (("claude-code", claude_code_roots()),):
        say(f"    {label:<12} looked in: {', '.join(str(r) for r in roots)}")
    say(f"    {'cowork':<12} looked in: desktop data dir (override CCBURN_COWORK_DIR)")
    for source in (CLAUDE_CODE, COWORK):
        found = by_source.get(source, [])
        say(f"    {source:<12} {len(found)} file(s)")

    if not files:
        say()
        say("    nothing found - every number below would be zero")
        return 1

    turns, duplicates = load(files)
    say(f"    parsed       {len(turns)} responses, {duplicates} content-block repeats collapsed")

    # --- coverage per source -------------------------------------------------
    say()
    say("  coverage")
    per_source: dict[str, set] = defaultdict(set)
    for t in turns:
        per_source[t.source].add(t.ts.astimezone(tz).date())
    for source, days in sorted(per_source.items()):
        ordered = sorted(days)
        say(f"    {source:<12} {len(ordered)} active day(s)   "
            f"{ordered[0]} .. {ordered[-1]}")

    all_days = sorted({d for s in per_source.values() for d in s})
    last = all_days[-1]
    gap = (today - last).days
    say()
    say("  streak check")
    say(f"    most recent local activity   {last}  ({gap} day(s) ago)")
    say(f"    today in {str(cfg.get('timezone')):<12}        {today}")
    if gap == 0:
        say("    -> streak counts from today")
    elif gap == 1:
        say("    -> streak counts from yesterday")
    else:
        say(f"    -> streak reads 0 because nothing local landed in the last 2 days.")
        say("       Claude Code on the web, Cowork remote and claude.ai chat write NO")
        say("       local logs, so daily use through those surfaces cannot raise this.")
        say("       Plan utilization below is the account-wide figure that does count them.")

    recent = [str(d) for d in all_days[-10:]]
    say(f"    last 10 local active days    {', '.join(recent)}")

    # gaps inside the covered range hint at pruning
    span = (all_days[-1] - all_days[0]).days + 1
    say(f"    covered span                 {span} calendar day(s), {len(all_days)} active")
    if span > 30 and len(all_days) < span * 0.4:
        say("    note: sparse coverage over a long span is what pruned history looks like")

    # --- history store -------------------------------------------------------
    say()
    say("  stored history")
    store = data_dir() / "history.json"
    if store.exists():
        try:
            blob = json.loads(store.read_text(encoding="utf-8"))
            rows = blob.get("days", {})
            days_stored = {k.split("|", 1)[0] for k in rows}
            say(f"    {store}")
            say(f"    {len(rows)} row(s) covering {len(days_stored)} day(s)")
            if days_stored:
                say(f"    range {min(days_stored)} .. {max(days_stored)}")
        except (OSError, json.JSONDecodeError):
            say("    unreadable")
    else:
        say("    none yet - it is written on the first run without --no-history")
        say("    it can only preserve days from now on; it cannot recover past pruning")

    # --- account level -------------------------------------------------------
    say()
    say("  plan utilization (account-wide, includes web and Cowork remote)")
    q, why = quota.read(cfg)
    if q is None:
        say(f"    unavailable - {why}")
    else:
        say(f"    via {q.source}")
        for w in q.windows:
            left = ""
            if w.remaining_seconds is not None:
                left = f"resets in {int(w.remaining_seconds // 3600)}h"
            say(f"    {w.label:<18} {w.utilization:5.1f}%  {left}")
    say()
    return 0
