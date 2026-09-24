"""Phase 6.5e: local security and the API contract."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import UTC
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibewatt import config, store
from vibewatt.api import create_app

ORIGIN = "http://127.0.0.1:8777"


def client(**cfg) -> TestClient:
    return TestClient(create_app({"offline": True, "quota": False, **cfg}))


# --- DNS rebinding and CSRF ---------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8777", "127.0.0.1.evil.example"]
)
def test_foreign_host_is_rejected(host):  # A-007
    resp = client().get("/api/health", headers={"host": host})
    assert resp.status_code == 421


@pytest.mark.parametrize("host", ["127.0.0.1:8777", "localhost:5173", "[::1]:8777"])
def test_loopback_hosts_are_served(host, logs):
    assert client().get("/api/health", headers={"host": host}).status_code == 200


def test_cross_site_text_plain_post_is_rejected(logs):  # A-004
    c = client()
    forged = c.post(
        "/api/harvest",
        content=json.dumps([{"id": "x"}]),
        headers={
            "content-type": "text/plain",
            "origin": "https://evil.example",
            "sec-fetch-site": "cross-site",
        },
    )
    assert forged.status_code == 403
    same_origin_text = c.post(
        "/api/harvest",
        content="[]",
        headers={
            "content-type": "text/plain",
            "origin": ORIGIN,
            "sec-fetch-site": "same-origin",
        },
    )
    assert same_origin_text.status_code == 403
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_same_origin_json_post_succeeds(logs):
    ok = client().post(
        "/api/harvest",
        json=[
            {
                "id": "s-1",
                "external_metadata": {"usage": {"input_tokens": 1, "cost_usd": 0.5}},
            }
        ],
        headers={"origin": ORIGIN, "sec-fetch-site": "same-origin"},
    )
    assert ok.status_code == 200 and ok.json()["written"] == 1


@pytest.mark.parametrize("path", ["/api/sync", "/api/analysis", "/api/weekly-summary"])
def test_bodiless_cross_site_post_is_rejected(path, logs):  # A-047
    resp = client().post(
        path, headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"}
    )
    assert resp.status_code == 403


# --- concurrency ------------------------------------------------------------------------


def test_reads_during_a_sync_never_fail(logs, monkeypatch):  # A-056
    from vibewatt import ingest

    c = client()
    assert c.post("/api/sync").status_code == 200
    Path(logs["claude-code"]).touch()  # changed, so the next sync re-reads it
    real = ingest.parse
    entered = threading.Event()

    def slow(*args):
        entered.set()
        time.sleep(1.5)
        return real(*args)

    monkeypatch.setattr(ingest, "parse", slow)
    worker = threading.Thread(target=lambda: c.post("/api/sync"))
    worker.start()
    assert entered.wait(5)
    try:
        for url in (
            "/api/summary",
            "/api/health",
            "/api/alerts",
            "/api/findings",
            "/api/sessions",
            "/api/quota",
        ):
            assert c.get(url).status_code == 200, url
        assert c.post("/api/sync").status_code == 409
    finally:
        worker.join(10)


def test_sync_streams_progress(logs):  # A-005
    resp = client().post("/api/sync", headers={"accept": "application/x-ndjson"})
    lines = [json.loads(line) for line in resp.text.splitlines()]
    assert lines[-1]["result"]["parsed"] == 2
    assert {"done", "total"} <= set(lines[0])


# --- the contract --------------------------------------------------------------------


def test_openapi_matches_the_committed_schema():  # A-033
    committed = Path(__file__).parents[1] / "web" / "openapi.json"
    live = create_app({"offline": True, "quota": False}).openapi()
    assert json.loads(committed.read_text(encoding="utf-8")) == live, (
        "run `npm run gen:api` in web/ and commit openapi.json and src/api/schema.d.ts"
    )
    assert "HarvestEnvelope" in live["components"]["schemas"]
    paths = live["paths"]
    assert "/api/usage" not in paths and "/api/dataset" not in paths
    for path, methods in paths.items():
        for method, op in methods.items():
            content = op["responses"].get("200", {}).get("content", {})
            assert content, f"{method.upper()} {path} has an untyped 200 response"


@pytest.mark.parametrize(
    "url",
    [
        "/api/sessions?from=0001-01-01",
        "/api/sessions?to=9999-12-31",
        "/api/summary?from=2026-09-20&to=2026-09-01",
        "/api/daily?source=nope",
        "/api/summary?metric=bogus",
        "/api/wrapped?year=1",
    ],
)
def test_bad_filters_are_4xx_never_500(url, logs):  # A-028, A-073
    status = client().get(url).status_code
    assert 400 <= status < 500, (url, status)


def test_active_block_first():  # A-074
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    c = client()
    with store.connect() as conn:
        for i, ago in enumerate((30, 20, 10, 0.5)):
            ts = (now - timedelta(hours=ago)).isoformat()
            conn.execute(
                f"INSERT INTO turns ({store.TURN_COLUMNS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"m{i}",
                    "r",
                    ts,
                    ts[:10],
                    "claude-code",
                    "p",
                    "s",
                    "claude-opus-5",
                    1,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    None,
                    0.1,
                    None,
                    0,
                ),
            )
        store.rebuild_rollup(conn)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('sync_tz', 'x')")
    c.app.state.synced = True
    blocks = c.get("/api/blocks").json()
    assert blocks[0]["is_active"] and not any(b["is_active"] for b in blocks[1:])
    starts = [b["start"] for b in blocks[1:]]
    assert starts == sorted(starts, reverse=True)


def test_alias_and_mask_filters_round_trip(logs):  # A-029, A-030
    aliased = client(project_aliases={"demo": "Demo App"})
    projects = aliased.get("/api/breakdown/project").json()
    assert "Demo App" in projects and "demo" not in projects
    assert (
        aliased.get("/api/summary", params={"project": "Demo App"}).json()["total"][
            "responses"
        ]
        == projects["Demo App"]["responses"]
    )
    assert "Demo App" in aliased.get("/api/session-facets").json()["projects"]

    masked = client(mask_projects=True)
    shown = set(masked.get("/api/breakdown/project").json())
    assert shown and all(name.startswith("project ") for name in shown)
    first = min(shown)
    assert masked.get("/api/summary", params={"project": first}).json()["total"][
        "responses"
    ]
    assert (
        masked.get("/api/summary", params={"project": "demo"}).json()["total"][
            "responses"
        ]
        == 0
    ), "a raw name must not unmask itself"
    rows = masked.get("/api/sessions").json()
    assert rows and all(
        r["project"].startswith("project ") or r["project"] == "-" for r in rows
    )
    detail = masked.get(f"/api/sessions/{rows[0]['id']}").json()
    assert detail["project"].startswith("project ")
    assert set(masked.get("/api/session-facets").json()["projects"]) <= shown | {"-"}


def test_model_filter_applies_everywhere(logs):  # A-035
    line = json.loads(
        Path(logs["claude-code"]).read_text(encoding="utf-8").splitlines()[0]
    )
    line["message"]["id"], line["requestId"] = "m-sonnet", "r-sonnet"
    line["message"]["model"] = "claude-sonnet-5"
    with Path(logs["claude-code"]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    c = client()
    params = {"model": "claude-sonnet-5"}
    summary = c.get("/api/summary", params=params).json()
    assert list(summary["by_model"]) == ["claude-sonnet-5"]
    assert summary["total"]["responses"] == 1
    assert (
        sum(b["responses"] for b in c.get("/api/daily", params=params).json().values())
        == 1
    )
    sessions = c.get("/api/sessions", params=params).json()
    assert sessions and all("claude-sonnet-5" in (s["model"] or "") for s in sessions)
    wrapped = c.get("/api/wrapped", params={**params, "year": 2026}).json()
    assert wrapped["local_summary"]["total"]["responses"] == 1


def test_summary_equals_the_cli_json(logs, tmp_path):  # A-076
    done = subprocess.run(
        [sys.executable, "-m", "vibewatt.cli", "json", "--offline", "--no-quota"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-400:]
    cli_total = json.loads(done.stdout)["total"]
    assert client().get("/api/summary").json()["total"] == cli_total


def test_csv_neutralizes_formula_cells(logs):  # A-072
    line = json.loads(
        Path(logs["claude-code"]).read_text(encoding="utf-8").splitlines()[0]
    )
    line["message"]["id"], line["requestId"] = "m-evil", "r-evil"
    line["message"]["model"] = '=HYPERLINK("https://evil.example")'
    with Path(logs["claude-code"]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    text = client().get("/api/export", params={"format": "csv"}).text
    assert "'=HYPERLINK" in text and ",=HYPERLINK" not in text


def test_harvest_validation(logs):  # A-027, A-031, A-032
    c = client()
    counts = c.post(
        "/api/harvest",
        json={
            "data": [
                {"external_metadata": {"usage": {"input_tokens": 1}}},  # no id
                {
                    "id": "local-bridge",
                    "environment_kind": "bridge",
                    "external_metadata": {"usage": {"input_tokens": 1}},
                },
                {
                    "id": "cloud-a",
                    "environment_kind": "anthropic_cloud",
                    "external_metadata": {"usage": {"input_tokens": 5}},
                },  # no cost
                {
                    "id": "cloud-b",
                    "external_metadata": {"usage": {"input_tokens": "x"}},
                },
            ]
        },
    ).json()
    assert counts == {
        "written": 1,
        "skipped": 1,
        "rejected_no_id": 1,
        "skipped_environment": 1,
    }
    rows = {r["id"]: r for r in c.get("/api/sessions").json() if r["harvested"]}
    assert rows["cloud-a"]["unpriced_turns"] == 1
    assert c.post("/api/harvest", json={"nothing": 1}).status_code == 400
    assert c.post("/api/harvest", json="not a listing").status_code == 422
    again = c.post(
        "/api/harvest",
        json={"data": [{"external_metadata": {"usage": {"input_tokens": 1}}}]},
    ).json()
    assert again["rejected_no_id"] == 1
    assert c.get("/api/sessions").status_code == 200


def test_cli_harvest_rejects_a_file_with_no_sessions(tmp_path):
    empty = tmp_path / "s.json"
    empty.write_text('{"unexpected": true}', encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "vibewatt.cli", "harvest", "--file", str(empty)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode != 0 and "no session entries" in done.stderr


def test_status_lookup_has_a_total_deadline(monkeypatch):  # A-095
    from vibewatt import service_status

    monkeypatch.setattr(service_status, "DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(service_status, "_expires", 0.0)
    monkeypatch.setattr(
        service_status, "_fetch", lambda: time.sleep(2) or {"indicator": "none"}
    )
    started = time.monotonic()
    assert service_status.read() is None
    assert time.monotonic() - started < 1.0


def test_tool_read_paths_are_keyed(tmp_path, logs):  # A-121
    import hashlib

    from vibewatt.ingest.tool_reads import read_tools

    session_file = tmp_path / "t.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-09-15T01:00:00Z",
                "cwd": "/w",
                "message": {
                    "model": "claude-opus-5",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "/w/secret.txt"},
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (row,) = read_tools("claude-code", session_file, key=b"k" * 32)
    guess = hashlib.sha256(b"s\0/w/secret.txt").hexdigest()
    assert row["path_hash"] != guess
    with store.connect() as conn:
        assert len(bytes.fromhex(store._path_key(conn).hex())) == 32
        assert store._path_key(conn) == store._path_key(conn)


def test_config_default_data_dir_is_isolated():
    assert "data" in str(config.data_dir())


def test_same_origin_verdict_wins_over_a_proxied_origin(logs):
    # Vite's proxy rewrites Host; the browser still says same-origin.
    resp = client().post(
        "/api/harvest",
        json=[
            {
                "id": "s-2",
                "external_metadata": {"usage": {"input_tokens": 1, "cost_usd": 0.5}},
            }
        ],
        headers={"origin": "http://127.0.0.1:5173", "sec-fetch-site": "same-origin"},
    )
    assert resp.status_code == 200
    no_verdict = client().post("/api/sync", headers={"origin": "http://127.0.0.1:5173"})
    assert no_verdict.status_code == 403
