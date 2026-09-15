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
    from ccburn.api import create_app

    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
