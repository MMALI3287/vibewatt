"""Serve the API over the test fixtures, never the developer's real ~/.claude.

Mirrors tests/conftest.py: every config and data directory points into a temp dir."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8778
    tmp = Path(tempfile.mkdtemp(prefix="vibewatt-e2e-"))
    for key, sub in (
        ("CLAUDE_CONFIG_DIR", "claude"),
        ("APPDATA", "appdata"),
        ("XDG_CONFIG_HOME", "appdata"),
        # A separate root avoids duplicate native/override discovery on Windows and Linux.
        ("VIBEWATT_COWORK_DIR", "cowork"),
        ("VIBEWATT_DATA_DIR", "data"),
        ("HOME", "home"),
        ("USERPROFILE", "home"),
    ):
        os.environ[key] = str(tmp / sub)

    layout = {
        FIXTURES / "activity.jsonl": tmp / "claude/history.jsonl",
        FIXTURES / "claude_code_session.jsonl": tmp
        / "claude/projects/demo/session.jsonl",
        FIXTURES / "cowork_audit.jsonl": tmp
        / "cowork/local-agent-mode-sessions/acct/space/id/audit.jsonl",
    }
    for src, dst in layout.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    import uvicorn

    from vibewatt import config as configmod
    from vibewatt import store
    from vibewatt.aggregate import cost_of
    from vibewatt.api import create_app
    from vibewatt.cli import report_zone
    from vibewatt.ingest import discover
    from vibewatt.ingest.tool_reads import read_tools

    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    cfg["plan_usd_per_month"] = 20
    # create_app captures the report zone, so it must be set here, not patched on
    # app.state afterwards. A fixed zone keeps results machine-independent; UTC+9
    # splits the 2026-09-15 fixture across two days so day navigation is testable.
    cfg["timezone"] = "Asia/Tokyo"
    with store.connect() as conn:
        store.sync_files(conn, discover(cfg), report_zone(cfg), cost_of)
        reads = [
            dict(r, session="s1", project="demo")
            for r in read_tools("claude-code", FIXTURES / "repeated_reads.jsonl")
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO tool_reads VALUES "
            "(:session,:tool_id,:ts,:source,:project,:model,:path_hash)",
            reads,
        )
        store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": f"cloud-{index:02d}",
                    "title": f"Cloud session {index:02d}",
                    "created_at": "2026-09-16T00:00:00Z",
                    "updated_at": "2026-09-16T01:00:00Z",
                    "origin": "web_claude_ai",
                    "session_context": {
                        "model": "cloud-only-model" if index == 0 else "claude-opus-5",
                        "sources": [
                            {
                                "git_repository": {
                                    "url": "https://github.com/example/cloud-project"
                                }
                            }
                        ],
                    },
                    "external_metadata": {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cost_usd": 1.23,
                        },
                        "context_usage": {
                            "used_tokens": 900000 if index == 1 else 451019,
                            "max_tokens": 1000000,
                        },
                    },
                }
                for index in range(45)
            ],
        )
    app = create_app(cfg)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
