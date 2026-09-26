"""Reproduce the figures Claude's own Stats show, to an exact moment.

    uv run python scripts/reconcile_stats.py --until 2026-09-15T19:13:45+09:00

The dashboard's reconciliation panel works on whole days. The desktop app's
usage panel is a snapshot taken at one moment, so checking it needs a cutoff
to the second. The counting rule is `vibewatt.sources.stats_line`, the same one
sync stores. Reads Claude Code logs only, as the Stats do.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from vibewatt.ingest import claude_code
from vibewatt.sources import stats_line


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--until", required=True, help="ISO time, inclusive to the second"
    )
    args = parser.parse_args()
    until = datetime.fromisoformat(args.until)
    limit = until.replace(microsecond=999_999)
    messages = tokens = 0
    sessions: set[str] = set()
    for path in claude_code.discover():
        try:
            handle = path.open("r", encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stats = stats_line(rec, path) if isinstance(rec, dict) else None
                if stats is None or stats[0] > limit:
                    continue
                sessions.add(stats[1])
                messages += stats[2]
                tokens += stats[3]
    print(f"sessions {len(sessions)}  messages {messages:,}  tokens {tokens:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
