"""d2 audit probes: every CLI command in a subprocess with isolated env, plus gate parity."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from vibewatt import config as configmod
from vibewatt.api import create_app

from test_d2_api import tree  # noqa: F401  (fixture)


def _env(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update({
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "APPDATA": str(tmp_path / "appdata"),
        "XDG_CONFIG_HOME": str(tmp_path / "appdata"), "VIBEWATT_DATA_DIR": str(tmp_path / "data"),
        "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"),
    })
    env.pop("VIBEWATT_COWORK_DIR", None)
    return env


def _run(tmp_path, *args, env=None):
    return subprocess.run([sys.executable, "-m", "vibewatt.cli", *args],
                          capture_output=True, env=env or _env(tmp_path), cwd=tmp_path, timeout=120)


def _short(p):
    err = p.stderr.decode("utf-8", "replace").strip().splitlines()
    return (p.returncode, err[-1][:160] if err else "", p.stdout[:80])


def test_every_cli_command(tree, tmp_path, capsys):  # noqa: F811
    # Title outside cp1252 to reproduce the deferred Windows stdout bug.
    a = tmp_path / "claude" / "projects" / "alpha" / "sa.jsonl"
    a.write_text(a.read_text(encoding="utf-8") + json.dumps(
        {"type": "last-prompt", "sessionId": "sb", "timestamp": "2026-09-11T05:00:00Z",
         "lastPrompt": "修正 the sync ✓"}, ensure_ascii=False) + "\n", encoding="utf-8")
    common = ["--offline", "--no-quota", "--tz", "utc"]
    good = tmp_path / "s.json"
    good.write_text(json.dumps({"data": [{"id": "session_X", "origin": "web_claude_ai",
                                          "created_at": "2026-09-15T00:00:00Z",
                                          "external_metadata": {"usage": {"input_tokens": 1,
                                                                          "output_tokens": 1,
                                                                          "cost_usd": 0.5}}}]}))
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    shape = tmp_path / "shape.json"
    shape.write_text(json.dumps({"sessions": [{"id": "z"}]}))
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps([{"id": "q", "external_metadata": "oops"}]))
    runs = {
        "sync": ("sync", *common),
        "report": ("report", *common),
        "report_by_project": ("report", "--by-project", "--mask-projects", *common),
        "blocks": ("blocks", *common),
        "statusline": ("statusline", *common),
        "json": ("json", *common),
        "csv": ("csv", *common),
        "html": ("html", "--out", str(tmp_path / "r.html"), *common),
        "doctor": ("doctor", *common),
        "harvest_nofile": ("harvest", *common),
        "harvest_good": ("harvest", "--file", str(good), *common),
        "harvest_good_again": ("harvest", "--file", str(good), *common),
        "harvest_badjson": ("harvest", "--file", str(bad), *common),
        "harvest_wrong_shape": ("harvest", "--file", str(shape), *common),
        "harvest_meta_not_dict": ("harvest", "--file", str(meta), *common),
        "harvest_missing": ("harvest", "--file", str(tmp_path / "nope.json"), *common),
        "sessions": ("sessions", *common),
        "tz_iana": ("json", "--offline", "--no-quota", "--tz", "Asia/Tokyo"),
        "tz_bogus": ("json", "--offline", "--no-quota", "--tz", "Mars/Base"),
        "since_bad": ("json", "--since", "2026-99-01", *common),
    }
    results = {name: _short(_run(tmp_path, *args)) for name, args in runs.items()}
    for k, v in results.items():
        print(f"{k:24} rc={v[0]} err={v[1]!r} out={v[2]!r}")
    tracebacks = {k: v for k, v in results.items() if "Traceback" in v[1] or "Error:" in v[1]}
    assert not tracebacks, tracebacks


def test_gate_summary_equals_cli_json(tree, tmp_path, capsys):  # noqa: F811
    cfg = configmod.load()
    cfg.update({"offline": True, "quota": False, "timezone": "utc"})
    client = TestClient(create_app(cfg))
    cases = [([], {}), (["--source", "claude-code"], {"source": "claude-code"}),
             (["--source", "cowork"], {"source": "cowork"}),
             (["--since", "2026-09-11"], {"from": "2026-09-11"})]
    diffs = []
    for cli_args, params in cases:
        p = _run(tmp_path, "json", "--offline", "--no-quota", "--tz", "utc", *cli_args)
        assert p.returncode == 0, p.stderr
        cli_total = json.loads(p.stdout)["total"]
        api_total = client.get("/api/summary", params=params).json()["total"]
        print(cli_args, "cli", cli_total["responses"], cli_total["cost_usd"],
              "api", api_total["responses"], api_total["cost_usd"])
        if cli_total != api_total:
            diffs.append((cli_args, cli_total, api_total))
    assert not diffs
