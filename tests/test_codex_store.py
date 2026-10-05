from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import JST

from vibewatt import identity, ingest, quota, store
from vibewatt.aggregate import cost_of, from_store

FIXTURE = Path(__file__).parent / "fixtures" / "codex_session.jsonl"
CHATGPT_ACCOUNT = "6f1c2d3e-4b5a-4c6d-8e7f-90a1b2c3d4e5"


def _codex_home(tmp_path, monkeypatch, *, account: str | None = CHATGPT_ACCOUNT):
    home = tmp_path / "codex"
    dst = home / "sessions" / "2026" / "09" / "10" / "rollout-1.jsonl"
    dst.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE, dst)
    if account is not None:
        (home / "auth.json").write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "id_token": "header.payload.signature",
                        "access_token": "secret-access",
                        "refresh_token": "secret-refresh",
                        "account_id": account,
                    },
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


@pytest.fixture
def both(tmp_path, logs, monkeypatch):
    """Claude Code and Cowork fixtures plus one Codex rollout."""
    _codex_home(tmp_path, monkeypatch)
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, cost_of)
        yield conn


def test_codex_rows_are_stored_under_their_own_source(both):
    rows = both.execute(
        "SELECT source, COUNT(*) FROM turns GROUP BY source ORDER BY source"
    ).fetchall()
    assert {r[0]: r[1] for r in rows} == {"claude-code": 2, "codex": 3, "cowork": 1}


def test_all_means_every_provider_with_a_per_source_split(both):
    everything = from_store(both, JST)
    assert set(everything.by_source) == {"claude-code", "cowork", "codex"}
    assert everything.total.turns == 6
    assert everything.by_source["codex"].turns == 3


def test_claude_scope_is_every_claude_surface_and_nothing_else(both):
    claude = from_store(both, JST, source="claude")
    assert set(claude.by_source) == {"claude-code", "cowork"}
    assert claude.total.turns == 3


def test_codex_report_is_selected_by_name(both):
    report = from_store(both, JST, source="codex")
    assert set(report.by_source) == {"codex"}
    assert report.total.turns == 3
    # 800 + 1500 + 700 cached and 200 + 500 + 300 fresh input, from the fixture.
    assert report.total.cache_read == 3000
    assert report.total.input == 1000


def test_session_list_follows_the_scope(both):
    every = {s["id"] for s in store.sessions(both, limit=100, tz=JST)}
    assert "codex-sess-1" in every
    claude = {s["id"] for s in store.sessions(both, limit=100, source="claude", tz=JST)}
    assert claude and "codex-sess-1" not in claude
    named = {s["id"] for s in store.sessions(both, limit=100, source="codex", tz=JST)}
    assert named == {"codex-sess-1"}


def test_cli_labels_each_provider_and_never_blends_them(both, capsys):
    from vibewatt import cli

    argv = ["--offline", "--no-quota", "--no-color", "--tz", "utc"]
    assert cli.main(["sync", *argv]) == 0
    sync_out = capsys.readouterr().out
    assert "claude-code" in sync_out and "codex" in sync_out
    assert "store now holds" not in sync_out  # one blended total hid the split

    assert cli.main([*argv, "--source", "codex"]) == 0
    codex_out = capsys.readouterr().out
    assert "Codex usage" in codex_out and "Claude usage" not in codex_out
    assert "local logs" in codex_out

    assert cli.main([*argv, "--source", "claude"]) == 0
    assert "Claude usage" in capsys.readouterr().out

    assert cli.main(argv) == 0
    every = capsys.readouterr().out
    assert "All usage" in every and "codex" in every


# --- ChatGPT identity -----------------------------------------------------


def test_chatgpt_account_is_read_from_auth_json(tmp_path, monkeypatch):
    _codex_home(tmp_path, monkeypatch)
    assert identity.chatgpt_account_id() == CHATGPT_ACCOUNT


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{not json",
        '{"tokens": {}}',
        '{"tokens": {"account_id": "not-a-uuid"}}',
    ],
)
def test_missing_or_bad_chatgpt_identity_is_unknown(tmp_path, monkeypatch, content):
    home = _codex_home(tmp_path, monkeypatch, account=None)
    if content is not None:
        (home / "auth.json").write_text(content, encoding="utf-8")
    assert identity.chatgpt_account_id() == "unknown"


# --- Codex plan limits ----------------------------------------------------

AFTER = datetime(2026, 9, 10, 2, tzinfo=UTC)
SCOPE = f"chatgpt:{CHATGPT_ACCOUNT}"


def test_codex_plan_readings_are_imported_once_per_change(both):
    rows = both.execute(
        "SELECT key, scope, utilization, label, source FROM quota_samples ORDER BY key"
    ).fetchall()
    # The fixture repeats one reading on six events: two windows, one row each.
    assert [tuple(r) for r in rows] == [
        ("codex_five_hour", SCOPE, 12.5, "Codex 5-hour (plus)", "codex-log"),
        ("codex_seven_day", SCOPE, 3.0, "Codex weekly (plus)", "codex-log"),
    ]


def test_codex_plan_never_appears_as_claude_plan_utilization(both):
    assert quota.latest(both, now=AFTER) is None
    codex = quota.latest(both, now=AFTER, provider="codex")
    assert {w.key for w in codex.windows} == {"codex_five_hour", "codex_seven_day"}
    assert {w.scope for w in codex.windows} == {SCOPE}


def test_a_resync_adds_no_duplicate_plan_rows(both, tmp_path):
    before = both.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0]
    rollout = next((tmp_path / "codex").rglob("rollout-*.jsonl"))
    rollout.write_text(rollout.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    store.sync_files(both, ingest.discover(), JST, cost_of)
    assert both.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0] == before


def test_free_plan_weekly_only_window_is_keyed_by_length(tmp_path):
    from vibewatt.ingest import codex

    path = tmp_path / "rollout-free.jsonl"
    event = {
        "timestamp": "2026-09-10T01:00:00.000Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": {
                "primary": {
                    "used_percent": 40.0,
                    "window_minutes": 10080,
                    "resets_at": 1789500000,
                },
                "secondary": None,
                "plan_type": "free",
            },
        },
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    [reading] = codex.plan_readings(path, "chatgpt:unknown")
    assert reading[1:5] == (
        "codex_seven_day",
        "Codex weekly (free)",
        "chatgpt:unknown",
        40.0,
    )


def test_dashboard_serves_codex_plan_apart_from_the_claude_plan(
    tmp_path, logs, monkeypatch
):
    from fastapi.testclient import TestClient

    from vibewatt import config
    from vibewatt.api import create_app

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AFTER.astimezone(tz)

    _codex_home(tmp_path, monkeypatch)
    monkeypatch.setattr(quota, "datetime", Clock)
    cfg = config.load()
    cfg.update(offline=True, quota=False, timezone="UTC")
    client = TestClient(create_app(cfg))
    client.app.state.ensure_synced()
    codex = client.get("/api/codex-quota").json()
    assert {w["key"] for w in codex["windows"]} == {
        "codex_five_hour",
        "codex_seven_day",
    }
    assert codex["provenance"]["scope"] == "chatgpt_account"
    claude = client.get("/api/quota").json()
    assert claude is None or not any(
        w["key"].startswith("codex_") for w in claude["windows"]
    )
