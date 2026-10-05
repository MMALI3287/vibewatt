"""Serve the dashboard over synthetic demo data for README screenshots.

    uv run python scripts/demo_server.py [port]

Writes six weeks of made-up Claude Code transcripts into a temp directory and
points every config and data path there, so the developer's real logs and store
are never read. The data is seeded, so captures are reproducible.
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "e2e"))
from fixture_server import isolate

PROJECTS = {
    "api-gateway": ["add rate limiting", "fix auth retry loop", "write load tests"],
    "mobile-app": ["dark mode polish", "offline sync queue", "crash on resume"],
    "docs-site": ["migrate to new theme", "fix broken links"],
    "data-pipeline": ["backfill october", "speed up joins", "schema drift alert"],
}
MODELS = [
    ("claude-opus-5", 0.55),
    ("claude-sonnet-5", 0.35),
    ("claude-haiku-4-5", 0.10),
]


def write_transcripts(root: Path, today: datetime) -> None:
    rng = random.Random(11)
    n = 0
    for day in range(42):
        date = today - timedelta(days=41 - day)
        # Quieter weekends and a busier final fortnight give the charts some shape.
        sessions = rng.randint(0, 2) if date.weekday() >= 5 else rng.randint(1, 4)
        sessions += 1 if day > 27 else 0
        for _ in range(sessions):
            project = rng.choice(list(PROJECTS))
            sid = f"demo-{n:04d}"
            model = rng.choices([m for m, _ in MODELS], [w for _, w in MODELS])[0]
            ts = date.replace(hour=rng.randint(9, 22), minute=rng.randint(0, 59))
            lines = []
            context = rng.randint(8_000, 20_000)
            for turn in range(rng.randint(12, 60)):
                n += 1
                ts += timedelta(seconds=rng.randint(20, 240))
                fresh = rng.randint(200, 3_000)
                usage = {
                    "input_tokens": rng.randint(3, 40),
                    "cache_creation_input_tokens": fresh,
                    "cache_read_input_tokens": context,
                    "output_tokens": rng.randint(150, 2_500),
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": fresh,
                        "ephemeral_1h_input_tokens": 0,
                    },
                }
                context += fresh
                lines.append(
                    {
                        "type": "assistant",
                        "requestId": f"req-{n}",
                        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "sessionId": sid,
                        "cwd": f"/work/{project}",
                        "message": {"id": f"msg-{n}", "model": model, "usage": usage},
                    }
                )
            lines.append(
                {
                    "type": "last-prompt",
                    "sessionId": sid,
                    "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "lastPrompt": rng.choice(PROJECTS[project]),
                }
            )
            path = root / "claude/projects" / project / f"{sid}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", "utf-8")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8779
    tmp = Path(tempfile.mkdtemp(prefix="vibewatt-demo-"))
    isolate(tmp)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    write_transcripts(tmp, today)

    import uvicorn

    from vibewatt import config as configmod
    from vibewatt import store
    from vibewatt.aggregate import cost_of
    from vibewatt.api import create_app
    from vibewatt.cli import report_zone
    from vibewatt.ingest import discover

    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    cfg["plan_usd_per_month"] = 200
    with store.connect() as conn:
        store.sync_files(conn, discover(cfg), report_zone(cfg), cost_of)
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
