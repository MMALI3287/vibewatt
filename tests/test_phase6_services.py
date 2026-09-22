from __future__ import annotations

import io
import json
import subprocess
from datetime import date, datetime, timezone

import pytest

from vibewatt import concierge, service_status, store, weekly
from vibewatt.aggregate import Bucket, Report


def test_disabled_summary_does_no_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled summary performed work")

    monkeypatch.setattr(weekly.cli, "build_report", forbidden)
    monkeypatch.setattr(weekly, "urlopen", forbidden)
    assert weekly.generate({}, timezone.utc)["status"] == "disabled"


@pytest.mark.parametrize("include_names", [False, True])
def test_weekly_privacy_and_bounds(monkeypatch, include_names):
    report = Report(total=Bucket(turns=2, output=100))
    report.by_project["PRIVATE_PROJECT"] = report.total
    report.by_day[date(2026, 9, 21)] = report.total
    report.sessions.add("PRIVATE_SESSION_PROMPT")
    report.unknown_models.add("PRIVATE_MODEL")

    def build(cfg, tz, **filters):
        assert cfg["quota"] is False
        assert cfg["offline"] is True
        assert filters == {"date_from": date(2026, 9, 15), "date_to": date(2026, 9, 21)}
        return report, None, None, None, None

    def fetch(request, timeout):
        assert timeout == 15
        body = request.data.decode()
        assert ("PRIVATE_PROJECT" in body) is include_names
        assert "PRIVATE_SESSION_PROMPT" not in body
        assert "PRIVATE_MODEL" not in body
        assert json.loads(body)["max_tokens"] == 600
        return io.BytesIO(b'{"content":[{"type":"text","text":"Weekly brief"}]}')

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(weekly.cli, "build_report", build)
    monkeypatch.setattr(weekly, "urlopen", fetch)
    result = weekly.generate(
        {"ai_summary": {"enabled": True, "include_project_names": include_names}},
        timezone.utc,
        datetime(2026, 9, 21, tzinfo=timezone.utc),
    )
    assert result["status"] == "ready"
    assert result["text"] == "Weekly brief"


def test_enabled_summary_without_key_does_no_work(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        weekly.cli, "build_report", lambda *a, **kw: pytest.fail("no key")
    )
    assert (
        weekly.generate({"ai_summary": {"enabled": True}}, timezone.utc)["status"]
        == "unavailable"
    )


@pytest.mark.parametrize("failure", [False, True])
def test_status_success_and_failure_cached(monkeypatch, failure):
    calls = []
    ticks = [0.0]
    monkeypatch.setattr(service_status, "_expires", 0)
    monkeypatch.setattr(service_status.time, "monotonic", lambda: ticks[0])

    def fetch(url, timeout):
        calls.append(url)
        assert timeout == 3
        if failure:
            raise OSError("offline")
        return io.BytesIO(
            b'{"status":{"indicator":"none","description":"Operational"}}'
        )

    monkeypatch.setattr(service_status, "urlopen", fetch)
    first = service_status.read()
    assert (first is None) is failure
    assert service_status.read() == first
    assert len(calls) == 1
    ticks[0] = 301
    service_status.read()
    assert len(calls) == 2


def test_concierge_fixture_is_read_only(tmp_path, monkeypatch):
    project = tmp_path / "fixture"
    project.mkdir()
    (project / "TODO.md").write_text("- [ ] Finish fixture\n- [x] Completed\n")
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    monkeypatch.setattr(
        store, "sessions", lambda conn, **kwargs: [{"title": "Previous session"}]
    )
    before = {
        p.relative_to(project): p.read_bytes()
        for p in project.rglob("*")
        if p.is_file()
    }
    result = concierge.build(
        None, {"project_paths": {"fixture": str(project)}}, "fixture"
    )
    after = {
        p.relative_to(project): p.read_bytes()
        for p in project.rglob("*")
        if p.is_file()
    }
    assert before == after
    assert "Previous session" in result["text"]
    assert "Finish fixture" in result["text"]
    assert "Completed" not in result["text"]
    assert "?? TODO.md" in result["text"]
    assert result["notes"] == []


def test_concierge_unknown_path_keeps_title(monkeypatch):
    monkeypatch.setattr(
        store, "sessions", lambda conn, **kwargs: [{"title": "Resume me"}]
    )
    monkeypatch.setattr(
        concierge, "_git_status", lambda root: pytest.fail("guessed path")
    )
    result = concierge.build(None, {}, "unknown")
    assert "Resume me" in result["text"]
    assert result["notes"]


def test_concierge_rejects_oversize_todo(tmp_path, monkeypatch):
    (tmp_path / "TODO.md").write_bytes(b"- [ ] private\n" * 6000)
    monkeypatch.setattr(store, "sessions", lambda conn, **kwargs: [])
    monkeypatch.setattr(concierge, "_git_status", lambda root: "")
    result = concierge.build(None, {"project_paths": {"p": str(tmp_path)}}, "p")
    assert "private" not in result["text"]
    assert any("64 KiB" in note for note in result["notes"])
