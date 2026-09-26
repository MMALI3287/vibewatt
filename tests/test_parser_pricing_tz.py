"""Phase 6.5c: parser robustness, pricing lookup, timezone and day boundary."""

from __future__ import annotations

import codecs
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from vibewatt import cli, config, pricing, store, terminal
from vibewatt.aggregate import Report, cost_of, from_store
from vibewatt.ingest import walk
from vibewatt.sources import CLAUDE_CODE, TITLE_RANK, read_file, read_titles

TOKYO = ZoneInfo("Asia/Tokyo")


def rec(msg="m1", req="r1", ts="2026-09-15T01:00:00Z", **usage_and_fields):
    fields = {
        k: usage_and_fields.pop(k)
        for k in list(usage_and_fields)
        if k in ("cwd", "sessionId", "isSidechain", "type")
    }
    usage = {"input_tokens": 10, "output_tokens": 5, **usage_and_fields}
    out = {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": "s1",
        "requestId": req,
        "message": {"id": msg, "model": "claude-opus-5", "usage": usage},
    }
    out.update(fields)
    return json.dumps(out)


def write(path, *lines, bom=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    path.write_bytes((codecs.BOM_UTF8 if bom else b"") + text.encode("utf-8"))
    return path


# --- pricing -------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-5@20250929",
        "claude-sonnet-4-5[1m]",
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "jp.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "au.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ],
)
def test_provider_prefixes_and_snapshot_suffixes_resolve(model):  # A-012
    assert pricing.rate_for(model) == pricing.BUILTIN["claude-sonnet-4-5"]


@pytest.mark.parametrize(
    "model", ["claude-opus-4-9", "claude-sonnet-4-7", "claude-opus-5-1"]
)
def test_unknown_sibling_is_unpriced_not_borrowed(model):  # A-011
    assert pricing.rate_for(model) is None


def test_remote_table_fills_an_unknown_id(monkeypatch):  # A-011
    monkeypatch.setattr(
        pricing, "_remote", {"claude-opus-4-9": pricing.Rate(7, 8, 14, 0.7, 35)}
    )
    assert pricing.rate_for("claude-opus-4-9-20261201").input == 7


@pytest.mark.parametrize("bad", [0, -1e-6, float("nan"), float("inf"), "x", None])
def test_remote_rates_are_validated(bad):  # A-066
    payload = {
        "claude-new-1": {
            "litellm_provider": "anthropic",
            "input_cost_per_token": bad,
            "output_cost_per_token": 1e-5,
        }
    }
    assert pricing._parse_remote(payload) == {}


def test_fast_mode_without_a_fast_rate_is_unpriced():
    assert pricing.rate_for("claude-sonnet-5", fast=True) is None
    assert pricing.rate_for("claude-opus-5", fast=True).input == 10.0


@pytest.mark.parametrize(
    "geo,multiplier",
    [("us", 1.1), ("global", 1.0), ("not_available", 1.0), (None, 1.0)],
)
def test_only_us_inference_geo_changes_the_price(tmp_path, geo, multiplier):
    f = write(
        tmp_path / "a.jsonl",
        rec(
            input_tokens=1_000_000,
            output_tokens=0,
            **({"inference_geo": geo} if geo else {}),
        ),
    )
    (turn,) = read_file(CLAUDE_CODE, f)
    assert cost_of(turn) == pytest.approx(5.0 * multiplier)


# --- parser robustness -----------------------------------------------------------


def test_wrong_typed_field_skips_the_record_not_the_file(tmp_path):  # A-023
    f = write(
        tmp_path / "a.jsonl",
        rec(msg="ok1"),
        rec(msg="bad", output_tokens="lots"),
        "{not json",
        rec(msg="ok2", req="r2"),
    )
    drops: Counter = Counter()
    assert [t.key[0] for t in read_file(CLAUDE_CODE, f, drops)] == ["ok1", "ok2"]
    assert drops == {"bad_field": 1, "bad_json": 1}
    with store.connect(tmp_path / "db") as conn:
        store.sync_files(conn, [(CLAUDE_CODE, f)], UTC, cost_of)
        assert store.dropped_records(conn) == {"bad_field": 1, "bad_json": 1}


def test_unreadable_file_is_retried_not_checkpointed(tmp_path, monkeypatch):  # A-024
    f = write(tmp_path / "a.jsonl", rec())
    real_open = type(f).open

    def locked(self, *a, **kw):
        if self == f:
            raise PermissionError("locked by another process")
        return real_open(self, *a, **kw)

    with store.connect(tmp_path / "db") as conn:
        monkeypatch.setattr(type(f), "open", locked)
        first = store.sync_files(conn, [(CLAUDE_CODE, f)], UTC, cost_of)
        monkeypatch.setattr(type(f), "open", real_open)
        second = store.sync_files(conn, [(CLAUDE_CODE, f)], UTC, cost_of)
        assert (first.unreadable, first.parsed, second.parsed) == (1, 0, 1)
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1


def test_leading_bom_keeps_the_first_record(tmp_path):  # A-069
    f = write(
        tmp_path / "a.jsonl", rec(msg="first"), rec(msg="second", req="r2"), bom=True
    )
    assert [t.key[0] for t in read_file(CLAUDE_CODE, f)] == ["first", "second"]


def test_partial_cache_split_keeps_every_written_token(tmp_path):  # A-063
    f = write(
        tmp_path / "a.jsonl",
        rec(
            msg="split",
            cache_creation_input_tokens=900,
            cache_creation={"ephemeral_1h_input_tokens": 300},
        ),
        rec(msg="flat", req="r2", cache_creation_input_tokens=400),
    )
    split, flat = read_file(CLAUDE_CODE, f)
    assert (split.cache_5m, split.cache_1h) == (600, 300)
    assert (flat.cache_5m, flat.cache_1h) == (400, 0)


def test_project_without_cwd(tmp_path):  # A-065
    root = tmp_path / "projects" / "C--work-demo"
    sub = write(root / "sess-1" / "subagents" / "agent-1.jsonl", rec())
    carried = write(
        root / "sess-2.jsonl", rec(msg="a", cwd="/work/demo"), rec(msg="b", req="r2")
    )
    assert next(read_file(CLAUDE_CODE, sub)).project == "C--work-demo"
    assert [t.project for t in read_file(CLAUDE_CODE, carried)] == ["demo", "demo"]


def test_synthetic_turn_is_skipped_quietly(tmp_path):  # A-068
    line = json.loads(rec())
    line["message"]["model"] = "<synthetic>"
    f = write(tmp_path / "a.jsonl", json.dumps(line))
    drops: Counter = Counter()
    assert list(read_file(CLAUDE_CODE, f, drops)) == [] and not drops


def test_walk_lists_each_real_file_once(tmp_path):  # A-070
    root = tmp_path / "projects"
    write(root / "p" / "a.jsonl", rec())
    loop = root / "p" / "loop"
    if sys.platform == "win32":
        made = (
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(loop), str(root)],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    else:
        try:
            os.symlink(root, loop, target_is_directory=True)
            made = True
        except OSError:
            made = False
    if not made:
        pytest.skip("cannot create a directory link here")
    assert [p.name for p in walk(root, "*.jsonl")] == ["a.jsonl"]


# --- titles ------------------------------------------------------------------------


def test_title_priority_and_only_one_kept(tmp_path):  # A-059, A-060, A-067
    def t(kind, **kw):
        return json.dumps({"type": kind, "sessionId": "s1", **kw})

    user = json.dumps(
        {
            "type": "user",
            "sessionId": "s1",
            "message": {"content": [{"type": "text", "text": "first ask"}]},
        }
    )
    wrapper = json.dumps(
        {
            "type": "user",
            "sessionId": "s2",
            "message": {"content": "<command-name>/clear</command-name>"},
        }
    )
    fallback = json.dumps(
        {"type": "user", "sessionId": "s2", "message": {"content": "real ask"}}
    )
    cases = {
        "first-user": [user],
        "last-prompt": [
            user,
            t("last-prompt", lastPrompt="one"),
            t("last-prompt", lastPrompt="two"),
        ],
        "ai-title": [
            user,
            t("ai-title", aiTitle="AI name"),
            t("last-prompt", lastPrompt="later"),
        ],
        "custom-title": [
            t("ai-title", aiTitle="AI"),
            t("custom-title", customTitle="Mine"),
            t("ai-title", aiTitle="AI again"),
        ],
    }
    expected = {
        "first-user": "first ask",
        "last-prompt": "two",
        "ai-title": "AI name",
        "custom-title": "Mine",
    }
    for kind, lines in cases.items():
        f = write(tmp_path / f"{kind}.jsonl", *lines)
        (title,) = read_titles([(CLAUDE_CODE, f)])
        assert (title["kind"], title["text"]) == (kind, expected[kind])
    f = write(tmp_path / "wrapped.jsonl", wrapper, fallback)
    assert read_titles([(CLAUDE_CODE, f)])[0]["text"] == "real ask"
    assert max(TITLE_RANK, key=TITLE_RANK.get) == "custom-title"


def test_store_keeps_one_title_and_never_downgrades(tmp_path):
    a = write(
        tmp_path / "a.jsonl",
        rec(),
        json.dumps({"type": "custom-title", "sessionId": "s1", "customTitle": "Mine"}),
    )
    b = write(
        tmp_path / "b.jsonl",
        rec(msg="m2", req="r2"),
        json.dumps({"type": "last-prompt", "sessionId": "s1", "lastPrompt": "secret"}),
    )
    with store.connect(tmp_path / "db") as conn:
        for f in (a, b):
            store.sync_files(conn, [(CLAUDE_CODE, f)], UTC, cost_of)
        rows = conn.execute("SELECT session, text FROM titles").fetchall()
    assert [tuple(r) for r in rows] == [("s1", "Mine")]


# --- export ----------------------------------------------------------------------


def test_unpriced_rows_are_flagged_in_exports(tmp_path):  # A-026
    line = json.loads(rec())
    line["message"]["model"] = "mystery-model-9"
    f = write(tmp_path / "a.jsonl", json.dumps(line), rec(msg="m2", req="r2"))
    with store.connect(tmp_path / "db") as conn:
        store.sync_files(conn, [(CLAUDE_CODE, f)], UTC, cost_of)
        report = from_store(conn, UTC)
    csv_rows = cli.to_csv(report).splitlines()
    assert csv_rows[0].endswith(",cost_usd,unpriced_responses")
    mystery = next(r for r in csv_rows if "mystery-model-9" in r).split(",")
    assert mystery[-2:] == ["", "1"]
    payload = cli.serialize(report)
    assert payload["by_model"]["mystery-model-9"]["unpriced"] == 1
    assert payload["by_model"]["claude-opus-5"]["unpriced"] == 0


# --- timezone and day boundary --------------------------------------------------------


def test_iana_zone_resolves_on_every_platform():  # A-058
    assert cli.resolve_tz("Asia/Tokyo").utcoffset(
        datetime(2026, 1, 1, tzinfo=UTC)
    ) == timedelta(hours=9)


def test_named_zone_identity_is_stable_across_dst():  # A-113
    london = ZoneInfo("Europe/London")
    assert config.zone_id(london) == "Europe/London"
    fixed = timezone(timedelta(hours=9))
    assert "9:00:00" in config.zone_id(fixed)


def test_heatmap_uses_the_report_today(monkeypatch):  # A-055
    class NoToday(date):
        @classmethod
        def today(cls):
            raise AssertionError("heatmap read the system date")

    monkeypatch.setattr(terminal, "date", NoToday)
    report = Report()
    report.today = date(2030, 1, 1)
    assert terminal.heatmap(report, weeks=2, color=False)


def test_redirected_report_does_not_crash_on_windows_codepages(tmp_path, logs):  # A-057
    env = {**os.environ, "PYTHONIOENCODING": "", "PYTHONUTF8": "0"}
    env.pop("PYTHONIOENCODING")
    done = subprocess.run(
        [sys.executable, "-m", "vibewatt.cli", "report", "--offline", "--no-quota"],
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-400:]
    assert "■".encode() in done.stdout


def test_day_start_hour_keeps_a_late_night_on_one_day(tmp_path):
    # 20:00-04:00 JST, the owner's working hours, on the night of 15 September.
    stamps = ["2026-09-15T11:00:00Z", "2026-09-15T15:30:00Z", "2026-09-15T18:59:00Z"]
    f = write(
        tmp_path / "a.jsonl",
        *(rec(msg=f"m{i}", req=f"r{i}", ts=ts) for i, ts in enumerate(stamps)),
    )
    shifted = config.day_zone(TOKYO, 6)
    with store.connect(tmp_path / "db") as conn:
        store.sync_files(conn, [(CLAUDE_CODE, f)], TOKYO, cost_of)
        plain = from_store(conn, TOKYO)
        store.sync_files(conn, [(CLAUDE_CODE, f)], shifted, cost_of)
        report = from_store(conn, shifted)
        sessions = store.sessions(
            conn, date_from=date(2026, 9, 15), date_to=date(2026, 9, 15), tz=shifted
        )
    assert sorted(plain.by_day) == [date(2026, 9, 15), date(2026, 9, 16)]
    assert list(report.by_day) == [date(2026, 9, 15)]
    assert report.by_day[date(2026, 9, 15)].turns == 3
    assert sorted(report.by_hour) == [0, 3, 20]  # clock hours, not shifted ones
    report.today = date(2026, 9, 16)
    assert report.streaks() == (1, 1)
    assert "■" in terminal.heatmap(report, weeks=2, color=False, today=report.today)
    assert len(sessions) == 1


def test_day_start_hour_is_validated():
    with pytest.raises(ValueError):
        config.day_zone(TOKYO, 24)
