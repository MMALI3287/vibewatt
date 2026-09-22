"""d8 probe: Phase 2 Gate says /api/summary totals equal `ccburn json` totals
for the same filters. The suite only checks the unfiltered case in-process."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ccburn import cli as climod
from ccburn import config as configmod
from ccburn.api import create_app


def _api(params: dict) -> dict:
    cfg = configmod.load()
    cfg["offline"] = True
    cfg["quota"] = False
    return TestClient(create_app(cfg)).get("/api/summary", params=params).json()


def _cli(argv: list[str], capsys) -> dict:
    rc = climod.main(["json", "--offline", "--no-quota", *argv])
    out = capsys.readouterr().out
    assert rc in (0, None)
    return json.loads(out)


@pytest.mark.parametrize(
    "argv,params",
    [
        ([], {}),
        (["--source", "cowork"], {"source": "cowork"}),
        (["--source", "claude-code"], {"source": "claude-code"}),
        (["--since", "2026-09-16"], {"from": "2026-09-16"}),
    ],
)
def test_api_summary_equals_cli_json(logs, capsys, argv, params):
    cli = _cli(argv, capsys)
    api = _api(params)
    for k in ("responses", "cost_usd", "input", "output"):
        assert api["total"][k] == cli["total"][k], (k, api["total"][k], cli["total"][k])
    assert api["sessions"] == cli["sessions"]
