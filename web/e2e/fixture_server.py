"""Serve the API over the test fixtures, never the developer's real ~/.claude.

Mirrors tests/conftest.py: every config and data directory points into a temp dir."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8778
    tmp = Path(tempfile.mkdtemp(prefix="ccburn-e2e-"))
    for key, sub in (("CLAUDE_CONFIG_DIR", "claude"), ("APPDATA", "appdata"),
                     ("XDG_CONFIG_HOME", "appdata"), ("CCBURN_DATA_DIR", "data"),
                     ("HOME", "home"), ("USERPROFILE", "home")):
        os.environ[key] = str(tmp / sub)
    os.environ.pop("CCBURN_COWORK_DIR", None)

    layout = {
        FIXTURES / "claude_code_session.jsonl": tmp / "claude/projects/demo/session.jsonl",
        FIXTURES / "cowork_audit.jsonl":
            tmp / "appdata/Claude/local-agent-mode-sessions/acct/space/id/audit.jsonl",
    }
    for src, dst in layout.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    import uvicorn

    from ccburn import config as configmod
    from ccburn import store
    from ccburn.aggregate import cost_of
    from ccburn.api import create_app
    from ccburn.ingest import discover
    from ccburn.ingest.tool_reads import read_tools

    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    with store.connect() as conn:
        store.sync_files(conn, discover(cfg), timezone.utc, cost_of)
        reads = [dict(r, session="s1", project="demo") for r in
                 read_tools("claude-code", FIXTURES / "repeated_reads.jsonl")]
        conn.executemany("INSERT OR IGNORE INTO tool_reads VALUES "
                         "(:session,:tool_id,:ts,:source,:project,:model,:path_hash)", reads)
        store.upsert_cloud_sessions(conn, [{
            "id": f"cloud-{index:02d}", "title": f"Cloud session {index:02d}",
            "created_at": "2026-09-16T00:00:00Z",
            "updated_at": "2026-09-16T01:00:00Z",
            "origin": "web_claude_ai",
            "session_context": {
                "model": "cloud-only-model" if index == 0 else "claude-opus-5",
                "sources": [{"git_repository": {"url": "https://github.com/example/cloud-project"}}],
            },
            "external_metadata": {
                "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 1.23},
                "context_usage": {"used_tokens": 900000 if index == 1 else 451019, "max_tokens": 1000000},
            },
        } for index in range(45)])
    app = create_app(cfg)
    app.state.tz = timezone.utc
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
