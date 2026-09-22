from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

import vibewatt.cli as climod
from vibewatt import quota
from vibewatt.api import create_app
from vibewatt.config import load as load_config

GEN = Path(__file__).resolve().parents[3] / "d10" / "gen_scale.py"


def _run(tmp_path, monkeypatch, lines):
    root = tmp_path / "claude"
    subprocess.run([sys.executable, str(GEN), str(root), "100", str(lines)], check=True,
                   capture_output=True)
    counts = {"discover": 0, "load": 0, "quota_fetch": 0}
    od, ol = climod.discover, climod.load

    def d(*a, **k):
        counts["discover"] += 1
        return od(*a, **k)

    def l(*a, **k):
        counts["load"] += 1
        return ol(*a, **k)

    monkeypatch.setattr(climod, "discover", d)
    monkeypatch.setattr(climod, "load", l)
    monkeypatch.setattr(quota, "read_token", lambda: "fake")

    def f():
        counts["quota_fetch"] += 1
        return None
    monkeypatch.setattr(quota, "fetch", f)
    cfg = load_config()
    cfg["offline"] = True
    cfg["quota"] = True  # fetch is stubbed; counts calls only
    c = TestClient(create_app(cfg))
    out = {}
    for ep in ["/api/summary", "/api/daily", "/api/quota", "/api/breakdown/model", "/api/summary"]:
        before = dict(counts)
        t = time.perf_counter()
        r = c.get(ep)
        dt = time.perf_counter() - t
        assert r.status_code == 200, (ep, r.text[:200])
        out.setdefault(ep, []).append((round(dt, 3), {k: counts[k] - before[k] for k in counts}))
    c.post("/api/sync")
    t = time.perf_counter()
    c.get("/api/sessions")
    out["/api/sessions(store)"] = round(time.perf_counter() - t, 3)
    return out


def test_small(tmp_path, monkeypatch):
    print("\nLINES=20000", _run(tmp_path, monkeypatch, 20000))


def test_large(tmp_path, monkeypatch):
    print("\nLINES=100000", _run(tmp_path, monkeypatch, 100000))
