"""d6 probes: OAuth token handling, pricing fetch bounds and validation."""
from __future__ import annotations

import io
import json
import math
from contextlib import redirect_stdout

from fastapi.testclient import TestClient

from ccburn import cli as climod
from ccburn import config as configmod
from ccburn import pricing, quota
from ccburn.api import create_app

SENTINEL = "sk-ant-oat01-SENTINEL-TOKEN-XYZ"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_oauth_token_never_leaves_via_api_or_doctor(logs, monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        body = {"five_hour": {"utilization": 42.0, "resets_at": "2099-01-01T00:00:00Z"}}
        return _Resp(json.dumps(body).encode())

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", SENTINEL)
    monkeypatch.setattr(quota.urllib.request, "urlopen", fake_urlopen)
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = True
    client = TestClient(create_app(cfg))
    client.post("/api/sync")
    leaks = []
    for path in ("/api/summary", "/api/quota", "/api/health", "/api/usage", "/api/dataset", "/",
                 "/api/alerts", "/api/findings", "/api/export?format=json",
                 "/api/wrapped?year=2026", "/api/blocks"):
        r = client.get(path)
        if SENTINEL in r.text:
            leaks.append(path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        from ccburn import doctor
        doctor.run(cfg, climod.resolve_tz(cfg.get("timezone")))
    print("quota calls:", {u for u, _ in seen}, "leaks:", leaks,
          "doctor leak:", SENTINEL in buf.getvalue())
    assert seen and all(u == quota.USAGE_URL for u, _ in seen)
    assert all(a == f"Bearer {SENTINEL}" for _, a in seen)
    assert leaks == [] and SENTINEL not in buf.getvalue()


def test_pricing_fetch_reads_unbounded_body(monkeypatch):
    sizes = []

    class Big(_Resp):
        def read(self, n=-1):
            sizes.append(n)
            return super().read(n)

    big = b'{"pad": "' + b"x" * (8 * 1024 * 1024) + b'"}'
    monkeypatch.setattr(pricing.urllib.request, "urlopen", lambda url, timeout=None: Big(big))
    payload = pricing._fetch_remote()
    print("read() called with n =", sizes, "payload bytes accepted:", len(big))
    assert payload is not None and sizes == [-1]


def test_builtin_outranks_remote_but_remote_zero_and_nan_accepted(monkeypatch):
    remote = {
        "claude-opus-5": {"litellm_provider": "anthropic", "input_cost_per_token": 0,
                          "output_cost_per_token": 0},
        "claude-zero-9": {"litellm_provider": "anthropic", "input_cost_per_token": 0,
                          "output_cost_per_token": 0},
        "claude-nan-9": {"litellm_provider": "anthropic", "input_cost_per_token": "nan",
                         "output_cost_per_token": "-1e-6"},
    }
    monkeypatch.setattr(pricing, "_remote", pricing._parse_remote(remote))
    builtin = pricing.rate_for("claude-opus-5")
    zero = pricing.rate_for("claude-zero-9")
    nan = pricing.rate_for("claude-nan-9")
    print("builtin opus-5:", builtin, "| remote zero:", zero, "| remote nan:", nan)
    assert builtin.input > 0  # builtin wins
    assert zero is not None and zero.input == 0 and zero.output == 0
    assert math.isnan(nan.input) and nan.output < 0
