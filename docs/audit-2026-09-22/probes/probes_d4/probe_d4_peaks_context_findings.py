"""d4 probes: 7.10 peaks, 7.11 context, findings table/API semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from vibewatt import store
from vibewatt.analysis import analyze, context, peaks
from vibewatt.api import create_app

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)


def sample(ts: datetime, util: float, reset_h: float = 5, label: str = "five_hour"):
    return {
        "ts": ts.isoformat(),
        "label": label,
        "utilization": util,
        "resets_at": (ts + timedelta(hours=reset_h)).isoformat(),
    }


def monday_series(hour_utc: int, minute: int = 10):
    # Three Mondays (2026-08-31, 09-07, 09-14) hitting 100% at hour_utc,
    # each polled 4 times within one reset window, plus low-util noise elsewhere.
    out = []
    for week in range(3):
        base = datetime(2026, 8, 31, hour_utc, minute, tzinfo=UTC) + timedelta(weeks=week)
        reset = base + timedelta(hours=3)
        for poll in range(4):
            ts = base + timedelta(minutes=5 * poll)
            out.append({"ts": ts.isoformat(), "label": "five_hour", "utilization": 100.0,
                        "resets_at": reset.isoformat()})
        for d in range(1, 6):
            out.append(sample(base + timedelta(days=d), 40.0))
    return out


def test_peak_concentrated_monday_window_utc():
    got = peaks.detect(monday_series(14), UTC)
    print("UTC peaks:", [(f.title, f.metrics) for f in got])
    assert len(got) == 1
    assert got[0].metrics["weekday"] == 0 and got[0].metrics["hour"] == 14
    assert got[0].metrics["encounters"] == 3  # polling counted once per window


def test_peak_uses_report_timezone_across_midnight():
    # 20:10 UTC Monday == 05:10 Tuesday JST
    got = peaks.detect(monday_series(20), JST)
    print("JST peaks:", [f.title for f in got])
    assert len(got) == 1 and got[0].metrics["weekday"] == 1 and got[0].metrics["hour"] == 5


def test_peak_via_analyze_names_window_but_not_timezone():
    with store.connect() as conn:
        conn.executemany("INSERT INTO quota_samples VALUES (:ts,:label,:utilization,:resets_at)",
                         monday_series(20))
        res = analyze(conn, JST, now=NOW)
    pk = [f for f in res["findings"] if f["kind"] == "peak"]
    print("analyze peak:", [(f["title"], f["detail"][:60]) for f in pk])
    assert len(pk) == 1 and "Tuesday" in pk[0]["title"]
    print("tz named in finding:", any(s in pk[0]["detail"] + pk[0]["title"] for s in ("+09", "JST", "UTC")))


def harvested(conn, sid, used, maximum):
    conn.execute(
        "INSERT INTO sessions (id, title, origin, surface, project, model, started, ended,"
        " context_used, context_max, harvested, cost) VALUES (?,?,?,?,?,?,?,?,?,?,1,0.5)",
        (sid, "t", "web_claude_ai", "web", "p", "claude-opus-5",
         "2026-09-10T00:00:00+00:00", "2026-09-10T01:00:00+00:00", used, maximum),
    )


@pytest.mark.parametrize(
    "used,maximum,expected",
    [
        (451019, 1_000_000, None),
        (900_000, 1_000_000, "urgent"),
        (700_000, 1_000_000, None),
        (700_001, 1_000_000, "warning"),
        (850_000, 1_000_000, "warning"),
        (850_001, 1_000_000, "urgent"),
        (140_000, 200_000, None),
        (170_000, 200_000, "warning"),
        (5, 0, None),
        (5, -10, None),
        (5, None, None),
        (None, 100, None),
        (1_200_000, 1_000_000, "urgent"),
    ],
)
def test_context_boundaries_via_analyze(used, maximum, expected):
    with store.connect() as conn:
        harvested(conn, "h1", used, maximum)
        res = analyze(conn, UTC, now=NOW)
    ctx = [f for f in res["findings"] if f["kind"] == "context"]
    print(used, maximum, "->", [(f["severity"], f["metrics"]["utilization"]) for f in ctx])
    assert [f["severity"] for f in ctx] == ([expected] if expected else [])


def test_context_float_boundary_sweep():
    # Any max where used/max is mathematically exactly 0.7 or 0.85 must not flip.
    bad = []
    for m in range(20, 20000, 20):
        for frac, sev_above in ((0.7, None), (0.85, "warning")):
            used = round(m * frac)
            if used * 100 != m * int(frac * 100):
                continue
            got = context.detect([{"id": "x", "day": "d", "ended": None,
                                   "context_used": used, "context_max": m}])
            sev = got[0].severity if got else None
            if sev != sev_above:
                bad.append((used, m, sev))
    print("float boundary mismatches:", bad[:10], len(bad))
    assert not bad


def seed_turns(conn, n_sessions=3, project="alpha"):
    cols = ["msg_id", "request_id", "ts", "day", "source", "project", "session", "model",
            "input", "cache_5m", "cache_1h", "cache_read", "output", "thinking", "web_search",
            "sidechain", "fast", "geo", "cost"]
    rows = []
    for s in range(n_sessions):
        proj = project if s < 2 else "beta"
        for i in range(5):
            rows.append([f"m{s}-{i}", "r", f"2026-09-10T0{i}:00:00+00:00", "2026-09-10",
                         "claude-code", proj, f"s{s}", "claude-opus-5", 100_000, 0, 0, 0,
                         100, 0, 0, 0, 0, None, 1.0])
    conn.executemany(f"INSERT INTO turns ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)


def client():
    return TestClient(create_app({"offline": True, "quota": False, "timezone": "utc"}))


def test_findings_ids_stable_dismiss_restore_and_scope_per_filter():
    with store.connect() as conn:
        seed_turns(conn)
    c = client()
    first = c.get("/api/findings").json()["findings"]
    target = next(f for f in first if f["rule"] == "low_cache_hit" and f["subject"] == "s0")
    again = c.get("/api/findings").json()["findings"]
    assert [f["id"] for f in again] == [f["id"] for f in first]
    assert c.post(f"/api/findings/{target['id']}/dismiss", json={"dismissed": True}).status_code == 200
    assert target["id"] not in {f["id"] for f in c.get("/api/findings").json()["findings"]}
    # Same session evidence under a project filter that includes it:
    filtered = c.get("/api/findings?project=alpha").json()["findings"]
    same = [f for f in filtered if f["rule"] == "low_cache_hit" and f["subject"] == "s0"]
    print("after dismiss in unfiltered view, project=alpha shows s0 cache finding:",
          [(f["id"][:8], f["dismissed"]) for f in same])
    # explicit date range equal to data span also resets dismissal
    dated = c.get("/api/findings?from=2026-09-10&to=2026-09-10").json()["findings"]
    same_dated = [f for f in dated if f["rule"] == "low_cache_hit" and f["subject"] == "s0"]
    print("date-bounded view shows it again:", len(same_dated))
    with store.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        scopes = conn.execute("SELECT COUNT(DISTINCT scope) FROM findings").fetchone()[0]
    print("findings rows:", n, "scopes:", scopes)
    # restore
    assert c.post(f"/api/findings/{target['id']}/dismiss", json={"dismissed": False}).status_code == 200
    assert target["id"] in {f["id"] for f in c.get("/api/findings").json()["findings"]}
    assert same and not same[0]["dismissed"]


def test_findings_resolved_become_inactive_and_created_at_preserved():
    with store.connect() as conn:
        seed_turns(conn, n_sessions=1)
    c = client()
    first = {f["id"]: f for f in c.get("/api/findings").json()["findings"]}
    fid = next(i for i, f in first.items() if f["rule"] == "low_cache_hit")
    c.post(f"/api/findings/{fid}/dismiss", json={"dismissed": True})
    with store.connect() as conn:
        conn.execute("UPDATE turns SET cache_read = 10_000_000")  # evidence resolves
    assert fid not in {f["id"] for f in c.get("/api/findings?include_dismissed=true").json()["findings"]}
    with store.connect() as conn:
        conn.execute("UPDATE turns SET cache_read = 0")  # evidence returns
    back = {f["id"]: f for f in c.get("/api/findings?include_dismissed=true").json()["findings"]}
    assert back[fid]["dismissed"] and back[fid]["created_at"] == first[fid]["created_at"]
