"""Mutation guards from the 2026-09-22 audit (docs/AUDIT-2026-09-22.md, d9).

Each test fails under one mutant that survived the phase 1-6 suite. The
history.restore guard was dropped with history.py in Phase 6.5b; live-day
precedence over imported history is covered in test_dedup_store.py (A-022)."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from vibewatt import aggregate, config, pricing, sources, store
from vibewatt.aggregate import Bucket, Report, cost_of
from vibewatt.sources import Turn

JST = timezone(timedelta(hours=9))


def turn(**kw) -> Turn:
    base = {
        "source": "claude-code",
        "ts": datetime(2026, 9, 10, 3, tzinfo=UTC),
        "model": "claude-sonnet-4-5",
        "input": 0,
        "cache_5m": 0,
        "cache_1h": 0,
        "cache_read": 0,
        "output": 0,
        "thinking": 0,
        "web_searches": 0,
        "fast": False,
        "geo": None,
        "sidechain": False,
        "project": "p",
        "session": "s",
        "key": ("m", "r"),
    }
    base.update(kw)
    return Turn(**base)


def line(
    msg_id,
    usage,
    model="claude-sonnet-4-5",
    ts="2026-09-10T03:00:00Z",
    session="s1",
    req=None,
    sidechain=False,
):
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "sessionId": session,
            "requestId": req or f"r-{msg_id}",
            "isSidechain": sidechain,
            "cwd": "/w/demo",
            "message": {"id": msg_id, "model": model, "usage": usage},
        }
    )


# --- pricing / cost (M3a, M4a, M29, M25, M5, M6, M26, M27) -----------------------


def test_cache_write_ttls_and_reads_priced_separately():
    # sonnet-4-5: 5m 3.75, 1h 6.00, read 0.30 per MTok
    assert cost_of(turn(cache_1h=1_000_000)) == pytest.approx(6.0)
    assert cost_of(turn(cache_5m=1_000_000)) == pytest.approx(3.75)
    assert cost_of(turn(cache_read=1_000_000)) == pytest.approx(0.30)


def test_web_search_billed_per_call():
    assert cost_of(turn(web_searches=3)) == pytest.approx(0.03)


def test_unknown_model_is_unpriced_not_zero():
    t = turn(model="mystery-model-9")
    assert cost_of(t) is None
    report = aggregate.build([t], tz=UTC)
    assert report.unknown_models == {"mystery-model-9"}
    assert report.total.unpriced == 1


def test_builtin_outranks_remote(monkeypatch):
    monkeypatch.setattr(
        pricing, "_remote", {"claude-sonnet-4-5": pricing.Rate(99, 99, 99, 99, 99)}
    )
    assert pricing.rate_for("claude-sonnet-4-5").input == 3.0


def test_longest_prefix_and_provider_prefix():
    assert pricing.rate_for("claude-opus-4-5-20251101").input == 5.0
    assert (
        pricing.rate_for("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        == pricing.BUILTIN["claude-sonnet-4-5"]
    )


# --- parser (M3b, M4b, M8b, M9b, M25b, M37, M32) ---------------------------------


def test_parser_reads_ttl_split_geo_fast_websearch(tmp_path):
    f = tmp_path / "s1.jsonl"
    f.write_text(
        "\n".join(
            [
                line(
                    "a",
                    {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_creation_input_tokens": 300,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 100,
                            "ephemeral_1h_input_tokens": 200,
                        },
                        "inference_geo": "us",
                        "speed": "fast",
                        "server_tool_use": {"web_search_requests": 2},
                    },
                    model="claude-opus-5",
                ),
                line(
                    "b",
                    {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 50,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 0,
                            "ephemeral_1h_input_tokens": 0,
                        },
                    },
                ),
                line("c", {"input_tokens": 1, "output_tokens": 1}, model="<synthetic>"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    turns = list(sources.read_file("claude-code", f))
    assert [t.key[0] for t in turns] == ["a", "b"]
    a, b = turns
    assert (a.cache_5m, a.cache_1h) == (100, 200)
    assert a.geo == "us" and a.fast is True and a.web_searches == 2
    assert (b.cache_5m, b.cache_1h) == (50, 0)


# --- timezone / today (M7a, M7b, M7d, M39, M40) ----------------------------------


def test_report_today_uses_report_timezone():
    # +14 and -12 are 26h apart, so the host date cannot match both.
    for tz in (timezone(timedelta(hours=14)), timezone(timedelta(hours=-12))):
        assert aggregate.build([], tz=tz).today == datetime.now(tz).date()


def test_aggregate_buckets_in_report_timezone():
    report = aggregate.build([turn(ts=datetime(2026, 9, 15, 20, tzinfo=UTC))], tz=JST)
    assert list(report.by_day) == [date(2026, 9, 16)]
    assert list(report.by_hour) == [5]


def test_build_report_date_from_uses_report_timezone(logs):
    from vibewatt import cli

    cfg = {**config.DEFAULTS, "offline": True, "quota": False}
    report, *_ = cli.build_report(cfg, JST, date_from=date(2026, 9, 16), refresh=True)
    # claude-code turns at 20:00Z/21:00Z on 09-15 are 09-16 in JST.
    assert report.total.turns == 2


def test_current_streak_counts_through_yesterday():
    r = Report()
    r.today = date(2026, 9, 20)
    for d in (date(2026, 9, 18), date(2026, 9, 19)):
        r.by_day[d].turns += 1
    assert r.streaks() == (2, 2)


def test_month_to_date_respects_year():
    r = Report()
    r.by_day[date(2025, 9, 5)].cost = 5.0
    r.by_day[date(2026, 9, 5)].cost = 1.0
    assert r.month_to_date(date(2026, 9, 20)).cost == pytest.approx(1.0)


# --- sidechains (M30) -------------------------------------------------------------


def test_exclude_sidechains_drops_them_from_totals():
    turns = [turn(key=("a", "1")), turn(key=("b", "2"), sidechain=True)]
    report = aggregate.build(turns, tz=UTC, include_sidechains=False)
    assert report.total.turns == 1
    assert report.subagent.turns == 1


# --- store: skip, migration, session detail, cloud (M11a, M12b, M12c, M24a, M24b, M36)


def test_sync_reparses_same_mtime_different_size(tmp_path):
    f = tmp_path / "logs" / "s1.jsonl"
    f.parent.mkdir()
    f.write_text(
        line("a", {"input_tokens": 1, "output_tokens": 1}) + "\n", encoding="utf-8"
    )
    st = os.stat(f)
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, [("claude-code", f)], UTC, cost_of)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(line("b", {"input_tokens": 1, "output_tokens": 1}) + "\n")
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
        store.sync_files(conn, [("claude-code", f)], UTC, cost_of)
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


def test_v4_migration_keeps_turns_and_harvested_sessions(tmp_path):
    db = tmp_path / "db.sqlite"
    f = tmp_path / "s1.jsonl"
    f.write_text(
        line("a", {"input_tokens": 1, "output_tokens": 1}) + "\n", encoding="utf-8"
    )
    with store.connect(db) as conn:
        store.sync_files(conn, [("claude-code", f)], UTC, cost_of)
        store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": "cloud-1",
                    "created_at": "2026-09-10T00:00:00Z",
                    "external_metadata": {
                        "usage": {"input_tokens": 5, "cost_usd": 1.5}
                    },
                }
            ],
        )
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '3')")
    with store.connect(db) as conn:
        assert store.schema_version(conn) == store.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM sessions WHERE harvested=1").fetchone()[
                0
            ]
            == 1
        )


def test_session_detail_totals_equal_sum_of_turns(tmp_path):
    f = tmp_path / "s1.jsonl"
    f.write_text(
        "\n".join(
            [
                line(
                    "a",
                    {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 1000,
                    },
                ),
                line(
                    "b",
                    {
                        "input_tokens": 30,
                        "output_tokens": 40,
                        "cache_read_input_tokens": 2000,
                    },
                    ts="2026-09-10T03:05:00Z",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with store.connect(tmp_path / "db.sqlite") as conn:
        store.sync_files(conn, [("claude-code", f)], UTC, cost_of)
        d = store.session_detail(conn, "s1")
    assert len(d["turns"]) == 2
    assert d["cost"] == pytest.approx(sum(t["cost"] for t in d["turns"]))
    assert d["tokens"] == sum(
        t["input"] + t["cache_5m"] + t["cache_1h"] + t["cache_read"] + t["output"]
        for t in d["turns"]
    )


def test_cloud_session_without_usage_is_skipped(tmp_path):
    with store.connect(tmp_path / "db.sqlite") as conn:
        assert store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": "x",
                    "created_at": "2026-09-10T00:00:00Z",
                    "external_metadata": {},
                }
            ],
        ) == {"written": 0, "skipped": 1, "rejected_no_id": 0, "skipped_environment": 0}
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# --- analysis (M13, M34) ------------------------------------------------------------


def test_anomaly_uses_median_mad_not_mean_stdev():
    from vibewatt.analysis.anomaly import detect

    target = date(2026, 9, 20)
    rows = [
        {
            "day": (target - timedelta(days=i)).isoformat(),
            "cost": 10.0 if i == 3 else 1.0,
        }
        for i in range(1, 29)
    ]
    rows.append({"day": target.isoformat(), "cost": 5.0})
    findings, enough = detect(rows, None, target)
    assert enough
    assert any(f.day == target.isoformat() for f in findings)


def test_burn_alert_needs_projection_over_100(tmp_path):
    from vibewatt.analysis.alerts import evaluate

    now = datetime(2026, 9, 21, 2, tzinfo=UTC)
    reset = (now + timedelta(hours=3)).isoformat()
    with store.connect(tmp_path / "a.db") as conn:
        for minutes, util in ((-10, 50), (-5, 51)):  # projects to ~88%
            conn.execute(
                "INSERT INTO quota_samples VALUES (?,?,?,?,?,?,?)",
                (
                    (now + timedelta(minutes=minutes)).isoformat(),
                    "five_hour",
                    "5-hour",
                    "account",
                    util,
                    reset,
                    "statusline",
                ),
            )
        assert [
            a for a in evaluate(conn, UTC, now=now)["alerts"] if a.get("kind") == "burn"
        ] == []


# --- wrapped (M22, M22b, M38) --------------------------------------------------------


def test_wrapped_days_and_hours_in_report_timezone(logs):
    from vibewatt.analysis.wrapped import build

    cfg = {**config.DEFAULTS, "offline": True, "quota": False}
    with store.connect() as conn:
        store.sync_files(
            conn,
            [("claude-code", logs["claude-code"]), ("cowork", logs["cowork"])],
            JST,
            cost_of,
        )
        result = build(conn, cfg, JST, 2026)
    # JST: cowork 11:00 on 09-15, claude-code 05:00/06:00 on 09-16.
    assert result["busiest_hour"] in {5, 6, 11}
    assert result["longest_streak"] == 2


def test_wrapped_ignores_cloud_twin_of_local_session(logs):
    # Reports read the store only since 6.5b, so the local session is synced
    # first; the guard is that its cloud twin is not counted a second time.
    from vibewatt import ingest
    from vibewatt.analysis.wrapped import build

    cfg = {**config.DEFAULTS, "offline": True, "quota": False}
    with store.connect() as conn:
        store.sync_files(conn, ingest.discover(), UTC, cost_of)
        store.upsert_cloud_sessions(
            conn,
            [
                {
                    "id": "s1",
                    "created_at": "2026-09-16T00:00:00Z",
                    "external_metadata": {"usage": {"input_tokens": 5, "cost_usd": 50}},
                }
            ],
        )
        result = build(conn, cfg, UTC, 2026)
    assert result["harvested_cost_usd"] == 0


# --- API model filter (M23a, M23c, M23d) ---------------------------------------------


def test_model_filter_applies_to_summary_sessions_wrapped(tmp_path):
    from fastapi.testclient import TestClient

    from vibewatt.api import create_app

    f = tmp_path / "claude" / "projects" / "demo" / "mixed.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(
        "\n".join(
            [
                line(
                    "a",
                    {"input_tokens": 10, "output_tokens": 10},
                    model="claude-opus-5",
                    session="s-opus",
                ),
                line(
                    "b",
                    {"input_tokens": 10, "output_tokens": 10},
                    model="claude-sonnet-4-5",
                    session="s-son",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    api = TestClient(
        create_app(
            {**config.DEFAULTS, "offline": True, "quota": False, "timezone": "utc"}
        )
    )
    assert api.post("/api/sync").status_code == 200
    q = "model=claude-sonnet-4-5"
    assert api.get(f"/api/summary?{q}").json()["total"]["responses"] == 1
    assert [s["id"] for s in api.get(f"/api/sessions?{q}").json()] == ["s-son"]
    assert (
        api.get(f"/api/wrapped?year=2026&{q}").json()["local_summary"]["total"][
            "responses"
        ]
        == 1
    )


def turn_at(ts, key):
    return Turn(
        source="claude-code",
        ts=ts,
        model="claude-sonnet-4-5",
        input=1,
        cache_5m=0,
        cache_1h=0,
        cache_read=0,
        output=1,
        thinking=0,
        web_searches=0,
        fast=False,
        geo=None,
        sidechain=False,
        project="p",
        session="s",
        key=key,
    )


def test_build_report_date_to_is_inclusive(logs):
    from vibewatt import cli

    cfg = {**config.DEFAULTS, "offline": True, "quota": False}
    report, *_ = cli.build_report(cfg, JST, date_to=date(2026, 9, 16), refresh=True)
    assert report.total.turns == 3


def test_block_starts_on_the_hour():
    blocks = aggregate.build_blocks(
        [
            turn_at(datetime(2026, 9, 10, 10, 30, tzinfo=UTC), ("a", "1")),
            turn_at(datetime(2026, 9, 10, 15, 10, tzinfo=UTC), ("b", "2")),
        ]
    )
    assert [b.start.hour for b in blocks] == [10, 15]


def test_idless_turns_are_not_collapsed(tmp_path):
    f = tmp_path / "s.jsonl"
    rec = lambda ts: json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "sessionId": "s",
            "message": {
                "model": "claude-sonnet-4-5",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
    )
    f.write_text(
        rec("2026-09-10T03:00:00Z") + "\n" + rec("2026-09-10T04:00:00Z") + "\n",
        encoding="utf-8",
    )
    turns, dups = sources.load([("claude-code", f)])
    assert (len(turns), dups) == (2, 0)


def test_plan_multiple_is_api_over_plan():
    r = Report()
    r.today = date(2026, 9, 20)
    r.by_day[date(2026, 9, 5)].cost = 100.0
    assert aggregate.plan_comparison(r, 20)["multiple"] == pytest.approx(5.0)


def test_cache_to_output_needs_200k():
    from vibewatt.analysis import waste

    row = {
        "day": "2026-09-10",
        "ts": "2026-09-10T03:00:00+00:00",
        "input": 0,
        "cache_5m": 0,
        "cache_1h": 0,
        "cache_read": 150_000,
        "output": 1,
        "sidechain": 0,
    }
    assert [
        f for f in waste.detect("s", [row], []) if f.rule == "cache_to_output"
    ] == []


def test_cache_hit_rate_counts_writes_in_denominator():
    b = Bucket(input=25, cache_5m=25, cache_read=50)
    assert b.cache_hit_rate == pytest.approx(0.5)
