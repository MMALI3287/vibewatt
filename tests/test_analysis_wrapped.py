"""Phase 6.5f: anomaly rule, findings snapshot, dismissals, Wrapped, reconciliation."""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from vibewatt import store
from vibewatt.aggregate import cost_of
from vibewatt.analysis import anomaly, peaks, waste
from vibewatt.api import create_app
from vibewatt.sources import CLAUDE_CODE, read_file, stats_line

UTC = timezone.utc


def day_rows(costs: dict[date, float]) -> list[dict]:
    return [{"day": d.isoformat(), "cost": c, "model": "claude-opus-5"} for d, c in costs.items()]


# --- the anomaly gate ------------------------------------------------------------------

def test_two_days_a_week_user_gets_no_anomalies():  # A-041
    start = date(2026, 3, 2)  # a Monday
    costs = {}
    for week in range(20):
        for offset, cost in ((1, 4.0), (3, 5.0)):  # Tuesdays and Thursdays
            costs[start + timedelta(weeks=week, days=offset)] = cost
    found, status = anomaly.detect(day_rows(costs), None, date(2026, 9, 1))
    assert found == [] and status["evaluated"] > 20


def test_thirty_flat_days_and_one_spike_flag_exactly_one():  # A-041, A-042
    costs = {date(2026, 8, 1) + timedelta(days=i): 3.0 + (i % 3) * 1e-12 for i in range(30)}
    costs[date(2026, 8, 31)] = 30.0
    found, _ = anomaly.detect(day_rows(costs), None, date(2026, 9, 1))
    assert [f.subject for f in found] == ["2026-08-31"]


def test_robust_threshold_not_mean_and_stdev():  # A-045
    # One wild day in the baseline inflates a stdev rule; median/MAD ignores it.
    baseline = [10.0] * 20 + [500.0]
    limit, center, _ = anomaly.threshold(baseline)
    assert center == 10.0 and limit == pytest.approx(13.0)
    costs = {date(2026, 8, 1) + timedelta(days=i): c for i, c in enumerate(baseline)}
    costs[date(2026, 8, 22)] = 20.0  # far under mean + 2 * stdev (~225)
    found, _ = anomaly.detect(day_rows(costs), date(2026, 8, 22), date(2026, 8, 22))
    assert len(found) == 1


# --- findings snapshot and dismissals ----------------------------------------------------

def _client(**cfg) -> TestClient:
    return TestClient(create_app({"offline": True, "quota": False, **cfg}))


def _seed_cache_finding(project: str = "demo", session: str = "s1") -> None:
    with store.connect() as conn:
        conn.execute(
            f"INSERT INTO turns ({store.TURN_COLUMNS}) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"m-{session}", "r", "2026-09-10T01:00:00+00:00", "2026-09-10", "claude-code",
             project, session, "claude-opus-5", 1_000_000, 0, 0, 0, 100, 0, 0, 0, 0, None,
             5.0, None, 1))
        store.rebuild_rollup(conn)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('generation', '1')")


def test_findings_get_reads_the_snapshot(monkeypatch):  # A-092
    from vibewatt import analysis

    _seed_cache_finding()
    c = _client()
    c.app.state.synced = True
    first = c.get("/api/findings").json()
    assert first["findings"]
    monkeypatch.setattr(analysis, "analyze",
                        lambda *a, **k: pytest.fail("a GET recomputed an unchanged snapshot"))
    fid = first["findings"][0]["id"]
    assert c.post(f"/api/findings/{fid}/dismiss", json={"dismissed": True}).status_code == 200
    again = c.get("/api/findings", params={"include_dismissed": True}).json()
    assert next(f for f in again["findings"] if f["id"] == fid)["dismissed"]
    assert c.get(f"/api/findings/{fid}").json()["dismissed"]
    monkeypatch.undo()
    assert c.post("/api/analysis").status_code == 200  # an explicit run recomputes


def test_session_dismissal_follows_the_finding_across_filters():  # A-091
    _seed_cache_finding()
    c = _client()
    c.app.state.synced = True
    cache = next(f for f in c.get("/api/findings").json()["findings"] if f["kind"] == "cache")
    c.post(f"/api/findings/{cache['id']}/dismiss", json={"dismissed": True})
    filtered = c.get("/api/findings", params={"project": "demo", "include_dismissed": True}).json()
    same = [f for f in filtered["findings"] if f["rule"] == cache["rule"]
            and f["subject"] == cache["subject"]]
    assert same and all(f["dismissed"] for f in same)
    assert not [f for f in c.get("/api/findings", params={"project": "demo"}).json()["findings"]
                if f["rule"] == cache["rule"]]


def test_cache_to_output_needs_200k_cache():  # A-094
    below = [{"input": 0, "cache_5m": 0, "cache_1h": 0, "cache_read": 199_000, "output": 10,
              "cost": 1.0, "model": "claude-opus-5", "ts": "2026-09-01T00:00:00+00:00",
              "sidechain": 0, "day": "2026-09-01", "session": "s", "project": "p"}]
    above = [dict(below[0], cache_read=250_000)]
    rules = {f.rule for f in waste.detect("s", above, [])}
    assert "cache_to_output" in rules
    assert "cache_to_output" not in {f.rule for f in waste.detect("s", below, [])}


def test_peak_finding_names_the_zone():  # A-122
    samples = [{"ts": f"2026-09-{d:02d}T20:00:00+00:00", "label": "5-hour", "utilization": 100,
                "resets_at": f"2026-09-{d:02d}T23:00:00+00:00"} for d in (1, 8)]
    (finding,) = peaks.detect(samples, ZoneInfo("Asia/Tokyo"))
    assert "Asia/Tokyo" in finding.title and "Asia/Tokyo" in finding.detail


# --- reconciliation ----------------------------------------------------------------------

def test_stats_line_rule(tmp_path):  # A-116, A-125
    lines = [
        {"type": "user", "timestamp": "2026-09-15T01:00:00Z", "sessionId": "s1"},
        {"type": "assistant", "timestamp": "2026-09-15T01:00:01Z", "sessionId": "s1",
         "requestId": "r", "message": {"id": "m", "model": "claude-opus-5",
                                       "usage": {"input_tokens": 10, "output_tokens": 5,
                                                 "cache_read_input_tokens": 999}}},
        # The same response's second content block repeats its usage.
        {"type": "assistant", "timestamp": "2026-09-15T01:00:02Z", "sessionId": "s1",
         "requestId": "r", "message": {"id": "m", "model": "claude-opus-5",
                                       "usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"type": "assistant", "timestamp": "2026-09-15T01:00:03Z", "sessionId": "s1",
         "isSidechain": True, "requestId": "r2",
         "message": {"id": "m2", "model": "claude-opus-5",
                     "usage": {"input_tokens": 7, "output_tokens": 3}}},
        {"type": "summary", "sessionId": "s1"},
    ]
    path = tmp_path / "s1.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    raw: dict = {}
    list(read_file(CLAUDE_CODE, path, raw=raw))
    (messages, tokens), = raw.values()
    assert messages == 3          # user + 2 main assistant lines; the subagent line is not a message
    assert tokens == 15 + 15 + 10  # no dedup, subagents in, cache out
    assert stats_line({"type": "summary"}, path) is None


def test_reconciliation_endpoint(logs):
    c = _client()
    body = c.get("/api/reconciliation").json()
    assert body["source"] == "claude-code"
    assert body["deduped"]["responses"] == 2       # the fixture's Claude Code responses
    assert body["stats_equivalent"]["messages"] == 3  # its three assistant lines
    assert body["stats_equivalent"]["tokens"] > body["deduped"]["input_output_tokens"]
    assert "session" in body["session_definition"].lower()


# --- Wrapped -------------------------------------------------------------------------------

def test_wrapped_buckets_days_hours_and_streak_in_the_report_zone(tmp_path):  # A-052, A-097
    from vibewatt.analysis.wrapped import build

    tokyo = ZoneInfo("Asia/Tokyo")
    stamps = [
        ("2026-02-28T14:30:00Z", "claude-opus-5", 100),    # 23:30 JST Feb 28
        ("2026-02-28T15:30:00Z", "claude-opus-5", 100),    # 00:30 JST Mar 1
        ("2026-03-01T15:10:00Z", "claude-sonnet-5", 900),  # 00:10 JST Mar 2
        ("2026-04-10T03:00:00Z", "claude-sonnet-5", 50),
    ]
    path = tmp_path / "logs" / "w.jsonl"
    path.parent.mkdir()
    path.write_text("\n".join(json.dumps({
        "type": "assistant", "timestamp": ts, "sessionId": "w1", "requestId": f"r{i}",
        "cwd": "/work/wrap", "message": {"id": f"m{i}", "model": model, "usage": {
            "input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": 1000}}})
        for i, (ts, model, out) in enumerate(stamps)) + "\n", encoding="utf-8")
    with store.connect() as conn:
        store.sync_files(conn, [(CLAUDE_CODE, path)], tokyo, cost_of)
        data = build(conn, {"offline": True}, tokyo, 2026)
    assert data["longest_streak"] == 3            # Feb 28, Mar 1, Mar 2 in JST
    assert data["busiest_day"] == "2026-03-02"
    assert data["busiest_hour"] == 0              # 00:10 JST, not 15:00 UTC
    assert {row["month"] for row in data["model_months"]} == {"2026-02", "2026-03", "2026-04"}
    assert data["cache_savings_usd"] and data["cache_savings_usd"] > 0


def test_weekly_summary_never_sends_stored_titles(monkeypatch, logs):  # A-099
    from vibewatt import weekly

    with store.connect() as conn:
        store.sync_files(conn, [(CLAUDE_CODE, logs["claude-code"])], UTC, cost_of)
        conn.execute("INSERT OR REPLACE INTO titles VALUES ('s1', 'custom-title', 4, NULL,"
                     " 'PRIVATE_TITLE_TEXT')")
    sent = []

    def fetch(request, timeout):
        sent.append(request.data.decode())
        return io.BytesIO(b'{"content":[{"type":"text","text":"ok"}]}')

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(weekly, "urlopen", fetch)
    result = weekly.generate({"ai_summary": {"enabled": True}}, UTC,
                             datetime(2026, 9, 18, tzinfo=UTC))
    assert result["status"] == "ready" and sent
    assert "PRIVATE_TITLE_TEXT" not in sent[0] and "fix the sync" not in sent[0]


def test_session_definition_is_documented():  # A-116
    assert "billable response" in store.SESSION_DEFINITION
    assert Path(__file__).parents[1].joinpath("docs", "PLAN.md").read_text(encoding="utf-8")
