from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_repricing import seed

from vibewatt import cli, store
from vibewatt.aggregate import cost_of, from_store
from vibewatt.analysis import context

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def test_local_context_latest_main_response_and_idle_expiry(tmp_path):
    with store.connect(tmp_path / "db") as conn:
        base = seed(conn, "claude-opus-4-5")
        old = replace(
            base, input=195000, ts=NOW - timedelta(minutes=20), key=("old", "r")
        )
        latest = replace(
            base,
            input=1,
            cache_read=160000,
            ts=NOW - timedelta(minutes=1),
            key=("new", "r"),
        )
        side = replace(base, input=199999, sidechain=True, ts=NOW, key=("side", "r"))
        conn.execute("DELETE FROM turns")
        store.upsert_turns(conn, [old, latest, side], UTC, cost_of)
        findings = context.local(conn, NOW)
        assert len(findings) == 1
        assert findings[0]["used_tokens"] == 160001
        assert findings[0]["max_tokens"] == 200000
        conn.execute(
            "INSERT INTO quota_samples(ts,label,utilization,key,scope,source) VALUES(?,?,?,'five_hour','account','oauth')",
            (NOW.isoformat(), "five_hour", 99),
        )
        assert context.local(conn, NOW) == findings
        assert context.local(conn, NOW + timedelta(minutes=31)) == []


def test_local_context_large_response_is_session_specific(tmp_path):
    with store.connect(tmp_path / "db") as conn:
        base = seed(conn, "claude-opus-4-6")
        conn.execute("DELETE FROM turns")
        store.upsert_turns(
            conn,
            [
                replace(base, input=800001, ts=NOW, session="large", key=("a", "r")),
                replace(base, input=160001, ts=NOW, session="normal", key=("b", "r")),
            ],
            UTC,
            cost_of,
        )
        found = {f["session"]: f["max_tokens"] for f in context.local(conn, NOW)}
        assert found == {"large": 1000000, "normal": 200000}


def test_history_sync_keeps_only_activity_and_is_idempotent(tmp_path, monkeypatch):
    from vibewatt import activity

    path = tmp_path / "claude" / "history.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text(Path("tests/fixtures/activity.jsonl").read_text())
    cli.sync_store({"offline": True}, UTC, [])
    cli.sync_store({"offline": True}, UTC, [])
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 2
        assert from_store(conn, UTC).total.turns == 0
        assert activity.calendar(conn, UTC, NOW)["longest_streak"] == 2
    assert b"PRIVATE_PROMPT_SENTINEL" not in store.db_path().read_bytes()


def test_history_bounds_before_storage(tmp_path, monkeypatch):
    from vibewatt import activity

    path = tmp_path / "history.jsonl"
    record = (
        json.dumps(
            {
                "timestamp": 1790679600000,
                "project": "demo",
                "display": "PRIVATE_PROMPT_SENTINEL",
            }
        )
        + "\n"
    )
    path.write_text(record * 3)
    monkeypatch.setattr(activity, "MAX_RECORDS", 2)
    with store.connect(tmp_path / "db") as conn:
        result = activity.sync(conn, [path])
        assert result["records"] == 2 and result["truncated"]
        monkeypatch.setattr(activity, "MAX_BYTES", len(record.encode()) - 1)
        result = activity.sync(conn, [path])
        assert result["records"] == 0 and result["truncated"]


def test_context_activity_api_reads_store_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vibewatt import activity
    from vibewatt.api import create_app

    with store.connect() as conn:
        activity.sync(conn, [Path("tests/fixtures/activity.jsonl")])
    app = create_app({"offline": True, "quota": False, "timezone": "UTC"})
    app.state.synced = True
    monkeypatch.setattr(
        activity,
        "sync",
        lambda *a: (_ for _ in ()).throw(AssertionError("request parsed history")),
    )
    client = TestClient(app)
    response = client.get("/api/activity")
    assert response.status_code == 200
    assert response.json()["longest_streak"] == 2
    assert client.get("/api/context").json() == []


def test_activity_report_day_and_malformed_rows(tmp_path):
    from vibewatt import activity
    from vibewatt.config import day_zone

    path = tmp_path / "history.jsonl"
    path.write_text(
        '[]\n{"timestamp":true,"project":"x"}\n{"timestamp":1790643600000,"project":"x","display":"PRIVATE_PROMPT_SENTINEL"}\n'
    )
    with store.connect(tmp_path / "db") as conn:
        stats = activity.sync(conn, [path])
        assert stats["records"] == 3
        assert stats["inserted"] == 1
        assert list(activity.calendar(conn, day_zone(UTC, 6), NOW)["days"]) == [
            "2026-09-28"
        ]


def test_context_thresholds_and_explicit_large_window(tmp_path):
    with store.connect(tmp_path / "db") as conn:
        base = seed(conn, "claude-opus-4-5")
        conn.execute("DELETE FROM turns")
        turns = [
            replace(base, input=size, ts=NOW, session=str(size), key=(str(size), "r"))
            for size in (140000, 140001, 170000, 170001)
        ]
        turns += [
            replace(
                base,
                input=850001,
                model="claude-opus-4-6[1m]",
                ts=NOW,
                session="large",
                key=("large", "r"),
            ),
            replace(
                base,
                input=190000,
                model="unknown",
                ts=NOW,
                session="unknown",
                key=("unknown", "r"),
            ),
        ]
        store.upsert_turns(conn, turns, UTC, cost_of)
        found = {f["session"]: f["severity"] for f in context.local(conn, NOW)}
        assert found == {
            "140001": "warning",
            "170000": "warning",
            "170001": "urgent",
            "large": "urgent",
        }
