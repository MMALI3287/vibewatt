"""d5 probes: Wrapped (PLAN 7.3) on an auditor-built fixture year."""

from __future__ import annotations

import json
import math
import os
from datetime import timedelta, timezone

from fastapi.testclient import TestClient

from vibewatt import config, store
from vibewatt.api import create_app

JST = timezone(timedelta(hours=9))
N = [0]


def turn(ts, sid, model="claude-sonnet-4-5", inp=100, out=50, cr=0, cwd="/work/alpha"):
    N[0] += 1
    mid = f"m{N[0]}"
    return json.dumps(
        {
            "type": "assistant",
            "requestId": "r" + mid,
            "timestamp": ts,
            "sessionId": sid,
            "cwd": cwd,
            "message": {
                "id": mid,
                "model": model,
                "usage": {
                    "input_tokens": inp,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cr,
                    "output_tokens": out,
                },
            },
        }
    )


def write(tmp_path, name, lines):
    path = tmp_path / "claude" / "projects" / name / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def api(tz=JST, **extra):
    app = create_app(
        {**config.DEFAULTS, "offline": True, "quota": False, "timezone": "utc", **extra}
    )
    app.state.tz = tz
    return TestClient(app)


def year_fixture(tmp_path):
    a = [
        # 2027-12-31 23:30 JST: belongs to 2027 in JST, 2027 in UTC too (14:30Z)
        turn("2027-12-31T14:30:00Z", "sA"),
        # 2028-01-01 00:30 JST = 2027-12-31T15:30Z: 2028 in JST, 2027 in UTC
        turn("2027-12-31T15:30:00Z", "sA"),
        turn("2028-01-02T03:00:00Z", "sA"),
        turn("2028-01-03T03:00:00Z", "sA"),
        # leap-day streak Feb 27..Mar 1 = 4 days (JST)
        turn("2028-02-27T03:00:00Z", "sB", model="claude-opus-5", cr=1_000_000),
        turn("2028-02-28T03:00:00Z", "sB", model="claude-opus-5"),
        turn("2028-02-29T03:00:00Z", "sB", model="claude-opus-5", inp=5000, out=2000),
        turn("2028-03-01T03:00:00Z", "sB", model="claude-opus-5"),
        # 2028-12-31 23:30 JST = 14:30Z: 2028
        turn("2028-12-31T14:30:00Z", "sC", cwd="/work/beta"),
        # 2029-01-01 00:30 JST = 2028-12-31T15:30Z: 2029 in JST, 2028 in UTC
        turn("2028-12-31T15:30:00Z", "sC", cwd="/work/beta", inp=99999),
    ]
    write(tmp_path, "alpha", a)


def test_own_year_fixture_all_shows_items(tmp_path):
    year_fixture(tmp_path)
    c = api(plan_usd_per_month=100)
    assert c.post("/api/sync").status_code == 200
    w = c.get("/api/wrapped?year=2028").json()
    s = c.get("/api/summary?from=2028-01-01&to=2028-12-31").json()
    print("WRAPPED", {k: w[k] for k in w if k not in ("local_summary", "notes")})
    # summary parity for live pipeline
    assert w["local_summary"]["total"] == s["total"]
    # stored headline equals live total when nothing pruned and nothing harvested
    live_tokens = sum(
        s["total"][k] for k in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
    )
    assert w["stored_tokens"] == live_tokens
    assert math.isclose(w["stored_cost_usd"], s["total"]["cost_usd"], abs_tol=1e-6)
    assert w["longest_streak"] == 4  # Feb 27..Mar 1 across leap day
    assert w["busiest_day"] == "2028-02-27"  # 1M cache read
    assert w["busiest_hour"] == 12  # 03:00Z = 12:00 JST
    assert w["biggest_session"]["id"] == "sB"
    months = {(m["month"], m["model"]) for m in w["model_months"]}
    assert ("2028-01", "claude-sonnet-4-5") in months
    assert ("2028-02", "claude-opus-5") in months and ("2028-03", "claude-opus-5") in months
    assert ("2027-12", "claude-sonnet-4-5") not in months
    # 2029-01-01 00:30 JST turn (inp 99999) must be excluded from 2028 in JST
    beta = [p for p in w["top_projects"] if p["name"] == "beta"]
    assert beta and beta[0]["tokens"] == 150
    assert math.isclose(w["cache_savings_usd"], 1_000_000 * (5.0 - 0.5) / 1e6, abs_tol=1e-6)
    assert w["annual_plan_usd"] == 1200.0
    assert math.isclose(w["api_equivalent_multiple"], w["stored_cost_usd"] / 1200.0)


def test_streak_uses_report_tz_not_utc(tmp_path):
    # JST days 06-10 (00:30 JST) and 06-11 (23:30 JST) are consecutive; UTC days are 06-09, 06-11
    write(tmp_path, "g", [turn("2028-06-09T15:30:00Z", "g1"), turn("2028-06-11T14:30:00Z", "g1")])
    jst = api(JST)
    jst.post("/api/sync")
    assert jst.get("/api/wrapped?year=2028").json()["longest_streak"] == 2
    utc = api(timezone.utc)
    assert utc.get("/api/wrapped?year=2028").json()["longest_streak"] == 1


def test_retention_pruned_transcripts(tmp_path):
    """Store keeps pruned history; does the Wrapped headline and live summary keep it?"""
    year_fixture(tmp_path)
    c = api()
    c.post("/api/sync")
    before = c.get("/api/wrapped?year=2028").json()
    summary_unfiltered_before = c.get("/api/summary").json()["total"]["cost_usd"]
    # Claude Code retention deletes the transcript
    for p in (tmp_path / "claude" / "projects").rglob("*.jsonl"):
        os.remove(p)
    assert c.post("/api/sync").status_code == 200
    after = c.get("/api/wrapped?year=2028").json()
    s_year = c.get("/api/summary?from=2028-01-01&to=2028-12-31").json()["total"]["cost_usd"]
    s_all = c.get("/api/summary").json()["total"]["cost_usd"]
    print(
        "RETENTION stored_cost before/after",
        before["stored_cost_usd"],
        after["stored_cost_usd"],
        "live before/after",
        before["local_summary"]["total"]["cost_usd"],
        after["local_summary"]["total"]["cost_usd"],
        "summary year",
        s_year,
        "summary unfiltered before/after",
        summary_unfiltered_before,
        s_all,
        "streak",
        before["longest_streak"],
        after["longest_streak"],
    )
    # the headline (stored) survives retention
    assert after["stored_cost_usd"] == before["stored_cost_usd"]
    assert after["stored_tokens"] == before["stored_tokens"]
    assert after["longest_streak"] == before["longest_streak"]
    # the live-log figure shown on the page drops to zero
    assert after["local_summary"]["total"]["cost_usd"] == 0


def test_never_synced_headline_is_empty(tmp_path):
    year_fixture(tmp_path)
    c = api()
    w = c.get("/api/wrapped?year=2028").json()
    print("NOSYNC stored", w["stored_cost_usd"], "live", w["local_summary"]["total"]["cost_usd"],
          "streak", w["longest_streak"], "busiest", w["busiest_day"])
    assert w["stored_cost_usd"] == 0 and w["local_summary"]["total"]["cost_usd"] > 0


def test_multiple_uses_twelve_months_for_current_year(tmp_path):
    write(tmp_path, "cur", [turn("2026-09-01T03:00:00Z", "c1", model="claude-opus-5", inp=1_000_000)])
    c = api(plan_usd_per_month=20)
    c.post("/api/sync")
    w = c.get("/api/wrapped?year=2026").json()
    print("MULTIPLE", w["stored_cost_usd"], w["annual_plan_usd"], w["api_equivalent_multiple"])
    assert w["annual_plan_usd"] == 240.0


def test_mask_projects_and_card_fields(tmp_path):
    year_fixture(tmp_path)
    c = api(mask_projects=True)
    c.post("/api/sync")
    w = c.get("/api/wrapped?year=2028").json()
    names = [p["name"] for p in w["top_projects"]]
    print("MASKED", names, w["biggest_session"])
    assert all(n.startswith("project ") for n in names)


def test_cloud_without_cost_and_started(tmp_path):
    year_fixture(tmp_path)
    c = api()
    c.post("/api/sync")
    with store.connect() as conn:
        store.upsert_cloud_sessions(
            conn,
            [
                {"id": "nostart", "origin": "web_claude_ai",
                 "external_metadata": {"usage": {"input_tokens": 10}}},
                {"id": "nocost", "created_at": "2028-05-01T00:00:00Z", "origin": "web_claude_ai",
                 "external_metadata": {"usage": {"input_tokens": 10}}},
            ],
        )
    r = c.get("/api/wrapped?year=2028")
    assert r.status_code == 200
    w = r.json()
    print("CLOUD", w["harvested_cost_usd"], w["api_equivalent_multiple"], w["unpriced_turns"])


def test_cloud_unknown_cost_counts_as_priced_zero(tmp_path):
    year_fixture(tmp_path)
    c = api(plan_usd_per_month=100)
    c.post("/api/sync")
    with store.connect() as conn:
        store.upsert_cloud_sessions(conn, [{
            "id": "nocost", "created_at": "2028-05-01T00:00:00Z", "origin": "web_claude_ai",
            "external_metadata": {"usage": {"input_tokens": 5_000_000, "output_tokens": 100_000}}}])
    w = c.get("/api/wrapped?year=2028").json()
    print("CLOUDZERO harvested", w["harvested_cost_usd"], "multiple", w["api_equivalent_multiple"],
          "unpriced", w["unpriced_turns"], "biggest", w["biggest_session"])
    # expected: a cloud session with no cost_usd is unknown, so the multiple is unavailable
    assert w["api_equivalent_multiple"] is None


def test_aliases_merging_two_projects(tmp_path):
    write(tmp_path, "a", [turn("2028-03-01T03:00:00Z", "x1", cwd="/work/a1"),
                          turn("2028-03-01T04:00:00Z", "x2", cwd="/work/a2")])
    c = api(project_aliases={"a1": "Same", "a2": "Same"})
    c.post("/api/sync")
    w = c.get("/api/wrapped?year=2028").json()
    live = c.get("/api/summary?from=2028-01-01&to=2028-12-31").json()
    print("ALIASES wrapped", w["top_projects"], "summary by_project", list(live.get("by_project", {}).keys()))
    assert [p["name"] for p in w["top_projects"]].count("Same") == 1


def test_year_extremes_do_not_500(tmp_path):
    year_fixture(tmp_path)
    c = TestClient(api().app, raise_server_exceptions=False)
    c.post("/api/sync")
    codes = {y: c.get(f"/api/wrapped?year={y}").status_code for y in (1, 9998)}
    west = TestClient(api(timezone(timedelta(hours=-5))).app, raise_server_exceptions=False)
    codes["9998w"] = west.get("/api/wrapped?year=9998").status_code
    codes["1w"] = west.get("/api/wrapped?year=1").status_code
    print("EXTREMES", codes)
    assert set(codes.values()) == {200}
