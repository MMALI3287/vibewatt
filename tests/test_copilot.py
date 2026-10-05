"""Phase 10: GitHub Copilot Chat usage from VS Code's chat-session journals."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import JST

from vibewatt import ingest, quota, store
from vibewatt.aggregate import cost_of, from_store
from vibewatt.ingest import copilot
from vibewatt.sources import COPILOT, dedupe

FIXTURE = Path(__file__).parent / "fixtures" / "copilot_session.jsonl"


@pytest.fixture
def vscode(tmp_path, monkeypatch):
    user = tmp_path / "Code - Insiders" / "User"
    workspace = user / "workspaceStorage" / "0123abcd"
    sessions = workspace / "chatSessions"
    sessions.mkdir(parents=True)
    (workspace / "workspace.json").write_text(
        json.dumps({"folder": "file:///c%3A/Users/dev/Projects/demo-app"}),
        encoding="utf-8",
    )
    dst = sessions / "copilot-sess-1.jsonl"
    shutil.copyfile(FIXTURE, dst)
    monkeypatch.setenv("VIBEWATT_VSCODE_USER_DIRS", str(user))
    monkeypatch.setenv("VIBEWATT_COPILOT_CACHE", str(tmp_path / "no-cache.json"))
    return dst


def _turns(path):
    drops: Counter = Counter()
    return sorted(copilot.parse(path, drops), key=lambda t: t.ts), drops


def test_discover_finds_workspace_and_empty_window_sessions(vscode, tmp_path):
    empty = (
        tmp_path
        / "Code - Insiders"
        / "User"
        / "globalStorage"
        / "emptyWindowChatSessions"
    )
    empty.mkdir(parents=True)
    shutil.copyfile(FIXTURE, empty / "other.jsonl")
    found = {p.name for p in copilot.discover()}
    assert found == {"copilot-sess-1.jsonl", "other.jsonl"}
    assert (COPILOT, vscode) in ingest.discover()


def test_journal_is_replayed_into_one_turn_per_request(vscode):
    turns, drops = _turns(vscode)
    assert len(turns) == 2
    assert drops["no_usage"] == 1  # the request that never completed
    first, second = turns
    assert first.key == ("copilot:request_a", "response_request_a")
    assert first.session == "copilot-sess-1"
    assert first.ts == datetime(2026, 9, 15, 3, tzinfo=UTC)
    # The router id is replaced by the model that answered.
    assert (first.model, second.model) == (
        "mai-code-1.1-flash",
        "claude-haiku-4-5-20251001",
    )


def test_output_is_the_request_total_and_input_the_last_prompt(vscode):
    first, second = _turns(vscode)[0]
    # completionTokens sums every tool-call round; metadata.outputTokens is the
    # last round only and is never used when the total exists.
    assert (first.input, first.output) == (41592, 4501)
    # The request-level prompt is missing on older entries; metadata has it.
    assert (second.input, second.output) == (32819, 721)
    assert first.cache_read == first.cache_5m == 0


def test_project_comes_from_the_workspace_folder(vscode):
    first = _turns(vscode)[0][0]
    assert first.project == "demo-app"


def test_billed_credits_are_the_cost_when_present(vscode):
    first, second = _turns(vscode)[0]
    assert first.billed_usd == pytest.approx(0.014563008)  # 1 credit = $0.01
    assert cost_of(first) == pytest.approx(0.014563008)
    assert second.billed_usd is None
    # Without credits the GitHub per-token rate gives an estimate: Haiku 4.5
    # at $1 in and $5 out.
    assert cost_of(second) == pytest.approx(32819 * 1 / 1e6 + 721 * 5 / 1e6)


def test_repeated_requests_dedupe_in_any_order(vscode):
    turns = list(copilot.parse(vscode)) * 2
    forward, dropped = dedupe(turns)
    backward, _ = dedupe(reversed(turns))
    assert len(forward) == 2 and dropped == 2
    assert sorted(forward, key=lambda t: t.key) == sorted(backward, key=lambda t: t.key)


def test_plain_json_snapshot_is_read_too(tmp_path):
    state = None
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        state = copilot.apply(state, entry)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert len(list(copilot.parse(path))) == 2


@pytest.mark.parametrize(
    "line",
    [
        '{"kind":1,"k":["requests",9,"completionTokens"],"v":5}',
        '{"kind":1,"k":["__proto__","x"],"v":1}',
        '{"kind":2,"k":["missing"],"v":[1]}',
        "{not json",
    ],
)
def test_a_bad_journal_entry_is_counted_not_fatal(vscode, line):
    vscode.write_text(
        vscode.read_text(encoding="utf-8") + line + "\n", encoding="utf-8"
    )
    turns, drops = _turns(vscode)
    assert len(turns) == 2
    assert sum(drops.values()) >= 2


def test_billed_cost_survives_repricing(tmp_path, vscode):
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, cost_of)
        conn.execute(
            "UPDATE meta SET value = 'stale' WHERE key = 'pricing_fingerprint'"
        )
        store.reprice(conn)
        cost = conn.execute(
            "SELECT cost FROM turns WHERE msg_id = 'copilot:request_a'"
        ).fetchone()[0]
        assert cost == pytest.approx(0.014563008)


def test_copilot_is_its_own_source_and_outside_the_claude_scope(tmp_path, vscode, logs):
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, cost_of)
        assert from_store(conn, JST, source="copilot").total.turns == 2
        assert "copilot" not in from_store(conn, JST, source="claude").by_source
        assert from_store(conn, JST).by_source["copilot"].turns == 2


def test_merge_keeps_the_billed_amount(vscode):
    first = _turns(vscode)[0][0]
    from vibewatt.sources import merge

    unbilled = replace(first, billed_usd=None)
    assert merge(unbilled, first).billed_usd == first.billed_usd
    assert merge(first, unbilled).billed_usd == first.billed_usd


# --- plan readings from Copilot's local entitlement cache -------------------

CACHE = """// Disposable cache for Copilot user responses, safe to delete.
{"copilotUserCache": {
  "v1:aaa": {"schemaVersion": 1, "retrievedAt": "2026-10-03T06:17:12.632Z",
    "response": {"login": "dev-personal", "copilot_plan": "individual",
      "quota_reset_date_utc": "2026-11-01T00:00:00.000Z",
      "quota_snapshots": {
        "chat": {"unlimited": true, "percent_remaining": 100, "entitlement": 0},
        "premium_interactions": {"unlimited": false, "entitlement": 300,
          "remaining": 75, "percent_remaining": 25.0}}}},
  "v1:bbb": {"schemaVersion": 1, "retrievedAt": "2026-09-17T02:52:10.465Z",
    "response": {"login": "dev-work", "copilot_plan": "business",
      "quota_reset_date_utc": "2026-10-01T00:00:00.000Z",
      "quota_snapshots": {
        "premium_interactions": {"unlimited": false, "entitlement": 3000,
          "remaining": 0, "percent_remaining": 0}}}}
}}
"""


def test_plan_readings_come_from_the_entitlement_cache(tmp_path, monkeypatch):
    path = tmp_path / "copilot-user-cache.json"
    path.write_text(CACHE, encoding="utf-8")
    monkeypatch.setenv("VIBEWATT_COPILOT_CACHE", str(path))
    rows = sorted(copilot.plan_readings())
    assert [r[1:6] for r in rows] == [
        (
            "copilot_premium",
            "Copilot premium requests (business)",
            "github:dev-work",
            100.0,
            "2026-10-01T00:00:00+00:00",
        ),
        (
            "copilot_premium",
            "Copilot premium requests (individual)",
            "github:dev-personal",
            75.0,
            "2026-11-01T00:00:00+00:00",
        ),
    ]


def test_copilot_plan_never_shows_as_claude_or_codex(tmp_path, monkeypatch, vscode):
    path = tmp_path / "copilot-user-cache.json"
    path.write_text(CACHE, encoding="utf-8")
    monkeypatch.setenv("VIBEWATT_COPILOT_CACHE", str(path))
    now = datetime(2026, 9, 20, tzinfo=UTC)
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, ingest.discover(), JST, cost_of)
        assert quota.latest(conn, now=now) is None
        assert quota.latest(conn, now=now, provider="codex") is None
        plan = quota.latest(conn, now=now, provider="copilot")
        assert {w.scope for w in plan.windows} == {
            "github:dev-work",
            "github:dev-personal",
        }


def test_missing_or_malformed_cache_gives_no_readings(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEWATT_COPILOT_CACHE", str(tmp_path / "absent.json"))
    assert copilot.plan_readings() == []
    bad = tmp_path / "bad.json"
    bad.write_text("// x\n{not json", encoding="utf-8")
    monkeypatch.setenv("VIBEWATT_COPILOT_CACHE", str(bad))
    assert copilot.plan_readings() == []
