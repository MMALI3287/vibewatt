from __future__ import annotations

import gzip
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_repricing import seed

from vibewatt import store
from vibewatt.aggregate import cost_of, from_store
from vibewatt.sources import Turn

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
M1 = "33333333-3333-4333-8333-333333333333"
M2 = "44444444-4444-4444-8444-444444444444"


@pytest.mark.parametrize("cloud_first", [True, False])
def test_machine_import_suppresses_overlapping_cloud_in_either_order(
    tmp_path, monkeypatch, cloud_first
):
    from vibewatt import identity, transfer
    from vibewatt.analysis import wrapped

    monkeypatch.setattr(identity, "machine_id", lambda: M1)
    cloud_archive, local_archive = tmp_path / "cloud.vwx", tmp_path / "local.vwx"
    with store.connect(tmp_path / "cloud.db") as conn:
        conn.execute(
            "INSERT INTO sessions(id,surface,model,started,ended,input,output,cost,harvested) "
            "VALUES ('same-session','cowork','claude-sonnet-4-6',"
            "'2026-09-29T00:00:00+00:00','2026-09-29T00:01:00+00:00',20,10,5,1)"
        )
        transfer.export_file(conn, cloud_archive)
    turn = Turn(
        "cowork",
        datetime(2026, 9, 29, tzinfo=UTC),
        "claude-sonnet-4-6",
        20,
        0,
        0,
        0,
        10,
        0,
        0,
        False,
        None,
        False,
        "demo",
        "same-session",
        ("msg", "request"),
    )
    with store.connect(tmp_path / "local.db") as conn:
        store.upsert_turns(conn, [turn], UTC, cost_of)
        store.rebuild_rollup(conn)
        transfer.export_file(conn, local_archive)
    monkeypatch.setattr(identity, "machine_id", lambda: M2)
    archives = (
        [cloud_archive, local_archive]
        if cloud_first
        else [local_archive, cloud_archive]
    )
    for archive in archives:
        transfer.import_file(archive, {}, UTC)
    with store.connect() as conn:
        sessions = store.sessions(conn)
        assert len(sessions) == 1 and not sessions[0]["harvested"]
        assert wrapped.build(conn, {}, UTC, 2026)["harvested_cost_usd"] == 0
        detail = store.session_detail(conn, "same-session")
        assert detail and not detail["harvested"]
        assert detail["cost"] == pytest.approx(cost_of(turn))
        counts = store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": "same-session",
                    "tags": ["cowork"],
                    "external_metadata": {
                        "usage": {"input_tokens": 20, "output_tokens": 10},
                        "cost_usd": 5,
                    },
                }
            ],
        )
        assert counts["written"] == 0
        assert counts["skipped_environment"] == 1


@pytest.mark.skipif(
    os.name != "nt", reason="Windows detects retained SQLite file handles"
)
@pytest.mark.parametrize("foreign", [False, True])
def test_identity_probes_release_database_for_relocation(tmp_path, foreign):
    from vibewatt import identity

    path = store.db_path()
    with store.connect() as conn:
        seed(conn)
    assert identity.owner(path) == "unknown"
    if foreign:
        with identity.scope(B):
            assert identity.foreign_records("turns", "msg_id")
    path.rename(tmp_path / "relocated.db")


def test_export_title_records_respect_import_record_cap(tmp_path, monkeypatch):
    from vibewatt import transfer

    archive = tmp_path / "bounded.vwx"
    monkeypatch.setattr(transfer, "MAX_RECORDS", 1)
    with store.connect() as conn:
        seed(conn)
        conn.execute("INSERT INTO titles VALUES ('s','custom-title',4,NULL,'title')")
        with pytest.raises(ValueError, match="record"):
            transfer.export_file(conn, archive, include_titles=True)
    assert not archive.exists()


def test_identity_migration_preserves_indexed_hour_lookup():
    with store.connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM turns WHERE substr(ts,1,13)=?",
            ("2026-09-15T00",),
        ).fetchall()
        assert any("USING INDEX turns_hr" in row[3] for row in plan)


def test_transfer_machine_identity_maxima_and_repeat(tmp_path, monkeypatch):
    from vibewatt import identity, transfer

    monkeypatch.setattr(identity, "account_id", lambda: A)
    monkeypatch.setattr(identity, "machine_id", lambda: M1)
    archive = tmp_path / "one.vwx"
    with store.connect(tmp_path / "source.db") as conn:
        turn = seed(conn)
        transfer.export_file(conn, archive)
    monkeypatch.setattr(identity, "machine_id", lambda: M2)
    with store.connect() as conn:
        seed(conn)
    transfer.import_file(archive, {}, UTC)
    with store.connect() as conn:
        assert from_store(conn, UTC).total.turns == 2
        assert {r[0] for r in conn.execute("SELECT machine_id FROM turns")} == {M1, M2}
        generation = store.generation(conn)
    transfer.import_file(archive, {}, UTC)
    with store.connect() as conn:
        assert from_store(conn, UTC).total.turns == 2
        assert store.generation(conn) == generation
    monkeypatch.setattr(identity, "machine_id", lambda: M1)
    with store.connect(tmp_path / "source.db") as conn:
        store.upsert_turns(conn, [replace(turn, output=20, input=1)], UTC, cost_of)
        transfer.export_file(conn, archive)
    monkeypatch.setattr(identity, "machine_id", lambda: M2)
    transfer.import_file(archive, {}, UTC)
    with store.connect() as conn:
        total = from_store(conn, UTC).total
        assert total.input == 2_000_000 and total.output == 20


def test_other_account_stays_separate_and_quota_never_fetched(tmp_path, monkeypatch):
    from vibewatt import identity, quota, transfer

    monkeypatch.setattr(identity, "account_id", lambda: A)
    with store.connect() as conn:
        seed(conn)
    with identity.scope(B), store.connect() as conn:
        seed(conn, key="b")
        seed(conn, key="b2")
        transfer.export_file(conn, tmp_path / "b.vwx")
    transfer.import_file(tmp_path / "b.vwx", {}, UTC)
    with store.connect() as conn:
        assert from_store(conn, UTC).total.turns == 1
    monkeypatch.setattr(
        quota, "fetch", lambda *a: pytest.fail("cross-account quota fetch")
    )
    monkeypatch.setattr(
        quota, "import_desktop", lambda *a: pytest.fail("cross-account desktop quota")
    )
    with identity.scope(B), store.connect() as conn:
        assert from_store(conn, UTC).total.turns == 2
        assert quota.refresh(conn, {}, allow_fetch=True)


def test_invalid_archive_rejected_before_store_write(tmp_path, monkeypatch):
    from vibewatt import transfer

    archive = tmp_path / "bad.vwx"
    archive.write_bytes(gzip.compress(json.dumps({"version": 999}).encode()))
    before = list(tmp_path.rglob("*.db"))
    with pytest.raises(ValueError):
        transfer.import_file(archive, {}, UTC)
    assert list(tmp_path.rglob("*.db")) == before
    monkeypatch.setattr(transfer, "MAX_BYTES", 20)
    archive.write_bytes(gzip.compress(b" " * 10000))
    with pytest.raises(ValueError):
        transfer.import_file(archive, {}, UTC)
    assert list(tmp_path.rglob("*.db")) == before


def test_machine_uuid_persists_and_account_config(tmp_path):
    from vibewatt import identity

    assert identity.machine_id() == identity.machine_id()
    path = tmp_path / "claude" / ".claude.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"oauthAccount": {"accountUuid": A}}))
    assert identity.account_id() == A


def test_cli_export_import_and_account_api(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from vibewatt import cli, identity
    from vibewatt.api import create_app

    monkeypatch.setattr(identity, "account_id", lambda: A)
    with store.connect() as conn:
        seed(conn)
    archive = tmp_path / "cli.vwx"
    assert cli.main(["export", "--out", str(archive), "--offline"]) == 0
    assert cli.main(["import", str(archive), "--offline"]) == 0
    with identity.scope(B), store.connect() as conn:
        seed(conn, key="b")
        seed(conn, key="b2")
    client = TestClient(create_app({"quota": False, "offline": True}))
    assert client.get("/api/accounts").json()["selected"] == A
    assert client.get("/api/summary").json()["total"]["responses"] == 1
    assert (
        client.get("/api/summary", headers={"X-Vibewatt-Account": B}).json()["total"][
            "responses"
        ]
        == 2
    )
    assert client.get("/api/summary").json()["total"]["responses"] == 1
    assert (
        client.get("/api/accounts", headers={"X-Vibewatt-Account": B}).json()[
            "selected"
        ]
        == B
    )
    assert (
        client.get(
            "/api/accounts", headers={"X-Vibewatt-Account": "../../x"}
        ).status_code
        == 400
    )


def test_foreign_account_sync_does_not_ingest_local_logs(tmp_path, monkeypatch):
    from vibewatt import cli, identity

    monkeypatch.setattr(identity, "account_id", lambda: A)
    with identity.scope(B):
        cli.sync_store(
            {"offline": True},
            UTC,
            [
                (
                    "claude-code",
                    __import__("pathlib").Path(
                        "tests/fixtures/claude_code_session.jsonl"
                    ),
                )
            ],
        )
        with store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


def test_switching_credentials_does_not_reassign_claimed_logs(tmp_path, monkeypatch):
    from vibewatt import cli, identity

    files = [("claude-code", Path("tests/fixtures/claude_code_session.jsonl"))]
    monkeypatch.setattr(identity, "account_id", lambda: A)
    cli.sync_store({"offline": True}, UTC, files)
    monkeypatch.setattr(identity, "account_id", lambda: B)
    cli.sync_store({"offline": True}, UTC, files)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


def test_export_titles_opt_in_and_bad_record_leaves_existing_store_untouched(tmp_path):
    from vibewatt import transfer

    archive = tmp_path / "private.vwx"
    with store.connect() as conn:
        seed(conn)
        conn.execute(
            "INSERT INTO titles VALUES ('s','custom-title',4,NULL,'PRIVATE_TITLE_SENTINEL')"
        )
        transfer.export_file(conn, archive)
        assert b"PRIVATE_TITLE_SENTINEL" not in gzip.decompress(archive.read_bytes())
        transfer.export_file(conn, archive, include_titles=True)
        assert b"PRIVATE_TITLE_SENTINEL" in gzip.decompress(archive.read_bytes())
    before = store.db_path().read_bytes()
    payload = json.loads(gzip.decompress(archive.read_bytes()))
    payload["turns"].append({"invalid": True})
    archive.write_bytes(gzip.compress(json.dumps(payload).encode()))
    with pytest.raises(ValueError):
        transfer.import_file(archive, {}, UTC)
    assert store.db_path().read_bytes() == before


def test_identified_account_does_not_adopt_unattributed_quota(monkeypatch):
    from vibewatt import identity, quota

    monkeypatch.setattr(identity, "account_id", lambda: A)
    monkeypatch.setattr(
        quota, "import_desktop", lambda *a: pytest.fail("unattributed desktop imported")
    )
    monkeypatch.setattr(
        quota, "from_statusline", lambda *a: pytest.fail("unattributed cache imported")
    )
    with store.connect() as conn:
        quota.refresh(
            conn,
            {"offline": True, "statusline_cache_path": "old-account.json"},
            allow_fetch=False,
        )


def test_suppressed_sidechain_import_is_idempotent(tmp_path, monkeypatch):
    from vibewatt import identity, transfer

    monkeypatch.setattr(identity, "machine_id", lambda: M1)
    with store.connect(tmp_path / "side.db") as conn:
        turn = seed(conn)
        conn.execute("DELETE FROM turns")
        store.upsert_turns(
            conn, [replace(turn, sidechain=True, key=("m", "side"))], UTC, cost_of
        )
        transfer.export_file(conn, tmp_path / "side.vwx")
    with store.connect() as conn:
        seed(conn)
        before = store.generation(conn)
    assert transfer.import_file(tmp_path / "side.vwx", {}, UTC)["changed"] == 0
    assert transfer.import_file(tmp_path / "side.vwx", {}, UTC)["changed"] == 0
    with store.connect() as conn:
        assert store.generation(conn) == before


def test_foreign_account_rebuckets_without_parsing_logs(monkeypatch):
    from datetime import datetime

    from vibewatt import cli, identity
    from vibewatt.config import day_zone

    monkeypatch.setattr(identity, "account_id", lambda: A)
    with identity.scope(B):
        with store.connect() as conn:
            turn = seed(conn)
            store.upsert_turns(
                conn,
                [replace(turn, ts=datetime(2026, 9, 15, 1, tzinfo=UTC))],
                UTC,
                cost_of,
            )
        cli.sync_store({"offline": True}, day_zone(UTC, 6), [])
        with store.connect() as conn:
            assert conn.execute("SELECT day FROM turns").fetchone()[0] == "2026-09-14"


def test_transfer_preserves_history_and_cloud_inputs(tmp_path, monkeypatch):
    from vibewatt import identity, transfer

    monkeypatch.setattr(identity, "machine_id", lambda: M1)
    with store.connect(tmp_path / "source.db") as conn:
        seed(conn)
        conn.execute(
            "INSERT INTO history_days(day,model,responses,input,cost) VALUES ('2026-01-01','claude-sonnet-5',2,1000,0.002)"
        )
        conn.execute(
            "INSERT INTO sessions(id,surface,model,started,ended,input,output,cost,harvested,raw,title) VALUES ('cloud','web','claude-sonnet-5','2026-01-02T00:00:00+00:00','2026-01-02T00:01:00+00:00',20,10,5,1,'PRIVATE_RAW_SENTINEL','PRIVATE_TITLE_SENTINEL')"
        )
        transfer.export_file(conn, tmp_path / "full.vwx")
        before = from_store(conn, UTC).total
    raw = gzip.decompress((tmp_path / "full.vwx").read_bytes())
    assert b"PRIVATE_RAW_SENTINEL" not in raw and b"PRIVATE_TITLE_SENTINEL" not in raw
    monkeypatch.setattr(identity, "machine_id", lambda: M2)
    transfer.import_file(tmp_path / "full.vwx", {}, UTC)
    assert transfer.import_file(tmp_path / "full.vwx", {}, UTC)["changed"] == 0
    with store.connect() as conn:
        after = from_store(conn, UTC).total
        assert (after.turns, after.input, after.cost) == (
            before.turns,
            before.input,
            before.cost,
        )
        assert (
            conn.execute("SELECT cost FROM sessions WHERE id='cloud'").fetchone()[0]
            == 5
        )
