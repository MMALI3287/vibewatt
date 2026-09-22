from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from vibewatt import store
from vibewatt.aggregate import cost_of
from vibewatt.analysis import analyze, anomaly, cache_scan, context, peaks, tips, waste
from vibewatt.ingest.tool_reads import read_tools

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "analysis_cases.json").read_text())
UTC = timezone.utc
NOW = datetime(2026, 9, 19, tzinfo=UTC)


def turn(**changes):
    row = {
        "msg_id": "m",
        "request_id": "r",
        "ts": "2026-09-01T00:00:00+00:00",
        "day": "2026-09-01",
        "source": "claude-code",
        "project": "demo",
        "session": "s",
        "model": "claude-opus-5",
        "input": 100,
        "cache_5m": 0,
        "cache_1h": 0,
        "cache_read": 0,
        "output": 100,
        "thinking": 0,
        "web_search": 0,
        "sidechain": 0,
        "fast": 0,
        "geo": None,
        "cost": 1.0,
    }
    row.update(changes)
    return row


def series(changes=None):
    return [
        turn(msg_id=f"m{i}", ts=f"2026-09-01T0{i}:00:00+00:00", **(changes or {}))
        for i in range(5)
    ]


def insert_turns(conn, rows):
    columns = list(turn())
    conn.executemany(
        f"INSERT INTO turns ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        [[r[k] for k in columns] for r in rows],
    )


@pytest.mark.parametrize("spike,expected", [(True, 1), (False, 0)])
def test_anomaly_trigger_and_flat_fixture(spike, expected):
    rows = [
        turn(
            day=(date(2026, 8, 1) + timedelta(days=i)).isoformat(),
            cost=10 if spike and i == 30 else 1,
        )
        for i in range(31)
    ]
    findings, enough = anomaly.detect(rows, None, date(2026, 9, 1))
    assert enough
    assert len(findings) == expected
    if findings:
        assert findings[0].metrics["baseline_days"] == 28
        assert findings[0].metrics["threshold_usd"] == 1


def test_anomaly_excludes_current_day_from_baseline_and_requires_14_days():
    rows = [
        turn(day=(date(2026, 8, 1) + timedelta(days=i)).isoformat(), cost=1)
        for i in range(14)
    ]
    assert anomaly.detect(rows, None, date(2026, 9, 1)) == ([], False)
    rows.append(turn(day="2026-08-15", cost=10))
    result, enough = anomaly.detect(rows, date(2026, 8, 15), date(2026, 8, 15))
    assert enough and len(result) == 1
    rows[0]["cost"] = None
    assert anomaly.detect(rows, None, date(2026, 8, 15)) == ([], False)


@pytest.mark.parametrize("case,expected", [("trigger", True), ("quiet", False)])
def test_cache_trigger_and_nontrigger_fixture(case, expected):
    results = cache_scan.detect("s", [turn(**CASES["cache"][case])], None)
    assert bool(results) == expected
    if expected:
        assert results[0].savings_usd == pytest.approx(4.5)


def test_cache_boundary_ttl_fast_geo_and_unknown_rates():
    assert cache_scan.detect("s", [turn(input=200_000, cache_read=200_000)], None) == []
    row = turn(
        input=1_000_000, cache_5m=1_000_000, cache_1h=1_000_000, fast=1, geo="us"
    )
    assert cache_scan.detect("s", [row], None)[0].savings_usd == pytest.approx(43.45)
    row["model"] = "unknown-model"
    result = cache_scan.detect("s", [row], None)[0]
    assert result.savings_usd is None
    assert result.metrics["pricing"] == "unpriced"
    overridden = cache_scan.detect(
        "s", [turn(input=1_000_000)], {"claude-opus-5": {"input": 2, "cache_read": 0.1}}
    )
    assert overridden[0].savings_usd == pytest.approx(1.9)


@pytest.mark.parametrize(
    "rule", ["cache_to_output", "subagent_heavy", "long_low_output"]
)
@pytest.mark.parametrize("case,expected", [("trigger", True), ("quiet", False)])
def test_waste_trigger_and_nontrigger_fixtures(rule, case, expected):
    rows = (
        series(CASES[rule][case])
        if rule != "cache_to_output"
        else [turn(**CASES[rule][case])]
    )
    assert (rule in {f.rule for f in waste.detect("s", rows, [])}) == expected


@pytest.mark.parametrize("rule", ["model_mix", "fast_mode"])
@pytest.mark.parametrize("case,expected", [("trigger", True), ("quiet", False)])
def test_tip_trigger_and_nontrigger_fixtures(rule, case, expected):
    results = tips.detect("s", series(CASES[rule][case]), [], None)
    assert (rule in {f.rule for f in results}) == expected


@pytest.mark.parametrize(
    "rule,changes",
    [("low_cache_hit", {"input": 1_000_000}), ("subagent_heavy", {"sidechain": 1})],
)
def test_advice_derived_from_triggering_findings(rule, changes):
    rows = series(changes)
    evidence = cache_scan.detect("s", rows, None) + waste.detect("s", rows, [])
    assert f"tip_{rule}" in {f.rule for f in tips.detect("s", rows, evidence, None)}
    assert f"tip_{rule}" not in {f.rule for f in tips.detect("s", series(), [], None)}


def test_fast_savings_respect_ttl_geo_and_unknown():
    rows = [
        turn(
            input=1_000_000,
            cache_5m=1_000_000,
            cache_1h=1_000_000,
            cache_read=1_000_000,
            output=1_000_000,
            fast=1,
            geo="us",
        )
    ]
    result = tips.detect("s", rows, [], None)[0]
    assert result.rule == "fast_mode"
    assert result.savings_usd == pytest.approx(51.425)
    rows[0]["model"] = "unknown-model"
    assert tips.detect("s", rows, [], None)[0].savings_usd is None


@pytest.mark.parametrize("case,expected", [("trigger", True), ("quiet", False)])
def test_context_trigger_and_nontrigger_fixture(case, expected):
    row = dict(
        id="cloud", day="2026-09-01", ended="2026-09-01", **CASES["context"][case]
    )
    findings = context.detect([row])
    assert bool(findings) == expected
    if expected:
        assert findings[0].severity == "urgent"


@pytest.mark.parametrize(
    "used,maximum,severity",
    [
        (700, 1000, None),
        (701, 1000, "warning"),
        (850, 1000, "warning"),
        (851, 1000, "urgent"),
        (900, 0, None),
        (None, 1000, None),
        (-1, 1000, None),
    ],
)
def test_context_boundaries(used, maximum, severity):
    result = context.detect(
        [
            {
                "id": "cloud",
                "day": "2026-09-01",
                "ended": None,
                "context_used": used,
                "context_max": maximum,
            }
        ]
    )
    assert (result[0].severity if result else None) == severity


def samples(utilization=100):
    return [
        {
            "ts": f"2026-09-{day:02d}T20:00:00+00:00",
            "label": "5-hour",
            "utilization": utilization,
            "resets_at": f"2026-09-{day:02d}T23:00:00+00:00",
        }
        for day in (1, 8)
    ]


@pytest.mark.parametrize("utilization,expected", [(100, 1), (99, 0)])
def test_peak_trigger_and_nontrigger_fixture(utilization, expected):
    result = peaks.detect(samples(utilization), timezone(timedelta(hours=9)))
    assert len(result) == expected
    if result:
        assert result[0].metrics["weekday"] == 2
        assert result[0].metrics["hour"] == 5


def test_peak_requires_distinct_windows_and_dates():
    first = samples()[0]
    assert peaks.detect([first, dict(first, ts="2026-09-01T20:10:00+00:00")], UTC) == []
    assert peaks.detect([dict(first, resets_at=None)] * 10, UTC) == []
    assert peaks.detect([dict(first, resets_at=first["ts"])] * 10, UTC) == []


@pytest.mark.parametrize("count,expected", [(3, True), (2, False)])
def test_repeated_reads_trigger_and_nontrigger(count, expected):
    reads = [
        {"path_hash": "hash", "day": "2026-09-01", "tool_id": f"id{i}"}
        for i in range(count)
    ]
    assert bool(waste.detect("s", [], reads)) == expected


def test_real_metadata_cycle_dedups_replays_backfills_and_retains_privacy(tmp_path):
    source = tmp_path / "session.jsonl"
    shutil.copyfile(FIXTURES / "repeated_reads.jsonl", source)
    replay = tmp_path / "replay.jsonl"
    shutil.copyfile(source, replay)
    path = tmp_path / "data.db"
    files = [("claude-code", source), ("claude-code", replay)]
    with store.connect(path) as conn:
        store.sync_files(conn, files, UTC, cost_of)
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM tool_reads").fetchone()[0] == 3
        assert (
            conn.execute("SELECT COUNT(DISTINCT path_hash) FROM tool_reads").fetchone()[
                0
            ]
            == 1
        )
        # Simulate a pre-upgrade usage checkpoint with no metadata checkpoint.
        conn.execute("DELETE FROM tool_reads")
        conn.execute("DELETE FROM tool_read_files")
        again = store.sync_files(conn, files, UTC, cost_of)
        assert again.skipped == 2 and again.parsed == 0
        assert conn.execute("SELECT COUNT(*) FROM tool_reads").fetchone()[0] == 3
        assert store.sync_files(conn, files, UTC, cost_of).skipped == 2
        result = analyze(conn, UTC, now=NOW)
        matches = [f for f in result["findings"] if f["rule"] == "repeated_reads"]
        assert len(matches) == 1 and matches[0]["metrics"]["read_calls"] == 3
        text = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM tool_reads"))
        assert "demo.py" not in text and "NEVER_STORE_CONTENT" not in text


def test_cowork_and_posix_metadata(tmp_path):
    path = tmp_path / "audit.jsonl"
    record = {
        "type": "assistant",
        "_audit_timestamp": "2026-09-01T01:00:00Z",
        "session_id": "cw",
        "cwd": "/work/app",
        "message": {
            "model": "claude-opus-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "a",
                    "name": "Read",
                    "input": {"file_path": "src/x.py"},
                },
                {
                    "type": "tool_use",
                    "id": "b",
                    "name": "Read",
                    "input": {"file_path": "/work/app/src/x.py"},
                },
            ],
        },
    }
    path.write_text(json.dumps(record))
    reads = list(read_tools("cowork", path))
    assert len(reads) == 2
    assert reads[0]["path_hash"] == reads[1]["path_hash"]
    assert reads[0]["session"] == "cw" and reads[0]["source"] == "cowork"


def test_read_only_metadata_does_not_link_to_a_missing_session():
    with store.connect() as conn:
        reads = list(read_tools("claude-code", FIXTURES / "repeated_reads.jsonl"))
        conn.executemany(
            "INSERT OR IGNORE INTO tool_reads VALUES "
            "(:session,:tool_id,:ts,:source,:project,:model,:path_hash)",
            reads,
        )
        result = analyze(conn, UTC, now=NOW)["findings"]
        assert len(result) == 1 and result[0]["rule"] == "repeated_reads"
        assert result[0]["session_id"] is None


def test_findings_stable_dismissed_resolved_and_restored():
    with store.connect() as conn:
        insert_turns(conn, [turn(input=1_000_000)])
        first = analyze(conn, UTC, now=NOW)["findings"]
        cache = next(f for f in first if f["kind"] == "cache")
        assert store.dismiss_finding(conn, cache["id"], True)
        again = analyze(conn, UTC, now=NOW)["findings"]
        assert {f["id"] for f in again} == {f["id"] for f in first}
        assert next(f for f in again if f["id"] == cache["id"])["dismissed"]
        conn.execute("UPDATE turns SET input=10")
        assert not analyze(conn, UTC, now=NOW)["findings"]
        assert (
            conn.execute(
                "SELECT active FROM findings WHERE id=?", (cache["id"],)
            ).fetchone()[0]
            == 0
        )
        conn.execute("UPDATE turns SET input=1000000")
        assert next(
            f for f in analyze(conn, UTC, now=NOW)["findings"] if f["id"] == cache["id"]
        )["dismissed"]
        assert store.dismiss_finding(conn, cache["id"], False)
        assert not store.dismiss_finding(conn, "missing", True)


def test_filters_timezone_lookback_and_unknown_costs():
    jst = timezone(timedelta(hours=9))
    with store.connect() as conn:
        insert_turns(
            conn,
            [
                turn(
                    input=1_000_000,
                    ts="2026-09-01T20:00:00+00:00",
                    cost=None,
                    model="unknown-model",
                )
            ],
        )
        selected = analyze(
            conn, jst, date_from=date(2026, 9, 2), date_to=date(2026, 9, 2), now=NOW
        )
        assert selected["findings"]
        assert all(f["day"] == "2026-09-02" for f in selected["findings"])
        assert any("Unpriced local models" in n for n in selected["notes"])
        for filters in (
            {"source": "web"},
            {"project": "other"},
            {"model": "other"},
            {"date_to": date(2026, 9, 1)},
        ):
            assert not analyze(conn, jst, now=NOW, **filters)["findings"]
        assert any("Local context is unavailable" in n for n in selected["notes"])


def test_cloud_context_retains_api_cost_and_peak_filters():
    with store.connect() as conn:
        store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": "cloud",
                    "created_at": "2026-09-01T00:00:00Z",
                    "origin": "web_claude_ai",
                    "external_metadata": {
                        "usage": {"cost_usd": 42.12345},
                        "context_usage": {
                            "used_tokens": 900_000,
                            "max_tokens": 1_000_000,
                        },
                    },
                }
            ],
        )
        conn.executemany(
            "INSERT INTO quota_samples VALUES (:ts,:label,:utilization,:resets_at)",
            samples(),
        )
        result = analyze(conn, UTC, now=NOW)
        assert {f["kind"] for f in result["findings"]} == {"context", "peak"}
        assert conn.execute("SELECT cost FROM sessions").fetchone()[0] == 42.12345
        assert not any(
            f["kind"] == "peak"
            for f in analyze(conn, UTC, source="web", now=NOW)["findings"]
        )


def test_v3_migration_preserves_existing_data_and_adds_metadata_checkpoints(tmp_path):
    path = tmp_path / "old.db"
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(store.SCHEMA)
    store._v2_files_and_quota_samples(conn)
    conn.execute("INSERT INTO meta VALUES ('schema','3')")
    conn.execute("INSERT INTO files VALUES ('retained',1,1,'ts',1)")
    conn.execute("INSERT INTO prompts VALUES ('s','ts','keep')")
    conn.commit()
    conn.close()
    with store.connect(path) as conn:
        assert store.schema_version(conn) == 4
        assert conn.execute("SELECT text FROM prompts").fetchone()[0] == "keep"
        assert conn.execute("SELECT path FROM files").fetchone()[0] == "retained"
        assert conn.execute("SELECT COUNT(*) FROM tool_read_files").fetchone()[0] == 0
