from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

import pytest

from vibewatt import ingest
from vibewatt.ingest import codex
from vibewatt.sources import CODEX, dedupe

FIXTURE = Path(__file__).parent / "fixtures" / "codex_session.jsonl"


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    day = home / "sessions" / "2026" / "09" / "10"
    day.mkdir(parents=True)
    dst = day / "rollout-2026-09-10T01-00-00-codex-sess-1.jsonl"
    shutil.copyfile(FIXTURE, dst)
    monkeypatch.setenv("CODEX_HOME", str(home))
    return dst


def test_discover_finds_active_and_archived_rollouts(codex_home, tmp_path):
    archived = tmp_path / "codex" / "archived_sessions"
    archived.mkdir()
    shutil.copyfile(FIXTURE, archived / "rollout-2026-08-01T00-00-00-old.jsonl")
    (archived / "notes.txt").write_text("not a rollout")
    found = {p.name for p in codex.discover()}
    assert found == {codex_home.name, "rollout-2026-08-01T00-00-00-old.jsonl"}
    assert (CODEX, codex_home) in ingest.discover()


def test_each_response_uses_last_usage_not_the_running_total(codex_home):
    turns = list(codex.parse(codex_home))
    kept, dropped = dedupe(turns)
    assert len(kept) == 3
    assert dropped == 1  # the identical event emitted twice
    first, second, third = sorted(kept, key=lambda t: t.ts)
    # OpenAI counts cached tokens inside input_tokens; vibewatt stores them apart.
    assert (first.input, first.cache_read, first.cache_5m, first.output) == (
        200,
        800,
        0,
        50,
    )
    assert (second.input, second.cache_read, second.cache_5m, second.output) == (
        500,
        1500,
        100,
        150,
    )
    assert (third.input, third.cache_read, third.output) == (300, 700, 60)
    assert (first.thinking, second.thinking, third.thinking) == (20, 40, 10)


def test_cumulative_totals_are_never_summed(codex_home):
    kept, _ = dedupe(codex.parse(codex_home))
    input_total = sum(t.input + t.cache_read for t in kept)
    # The log's last cumulative input is 4000. Summing the running totals would
    # give 1000 + 3000 + 4000.
    assert input_total == 4000


def test_model_follows_the_latest_turn_context(codex_home):
    kept, _ = dedupe(codex.parse(codex_home))
    assert [t.model for t in sorted(kept, key=lambda t: t.ts)] == [
        "gpt-6-astra",
        "gpt-6-astra",
        "gpt-5.5",
    ]


def test_marker_with_no_breakdown_is_not_a_response(codex_home):
    drops: Counter = Counter()
    list(codex.parse(codex_home, drops))
    assert drops["no_usage"] == 1


def test_result_does_not_depend_on_event_order(codex_home):
    turns = list(codex.parse(codex_home))
    forward, _ = dedupe(turns)
    backward, _ = dedupe(reversed(turns))
    assert sorted(forward, key=lambda t: t.key) == sorted(backward, key=lambda t: t.key)


def test_turn_identity_and_labels(codex_home):
    turn = next(iter(codex.parse(codex_home)))
    assert turn.source == CODEX
    assert turn.session == "codex-sess-1"
    assert turn.project == "demo"
    assert turn.version == "0.159.0"
    assert not turn.sidechain
    assert not turn.fast


def test_an_event_before_any_model_is_counted_not_priced_at_zero(tmp_path):
    path = tmp_path / "rollout-x.jsonl"
    path.write_text(
        '{"timestamp":"2026-09-10T01:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"s","cwd":"/w/p"}}\n'
        '{"timestamp":"2026-09-10T01:00:05.000Z","type":"event_msg","payload":'
        '{"type":"token_count","info":{"total_token_usage":{"input_tokens":10,'
        '"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":5,'
        '"reasoning_output_tokens":0,"total_tokens":15},"last_token_usage":'
        '{"input_tokens":10,"cached_input_tokens":0,"cache_write_input_tokens":0,'
        '"output_tokens":5,"reasoning_output_tokens":0,"total_tokens":15}}}}\n',
        encoding="utf-8",
    )
    drops: Counter = Counter()
    assert list(codex.parse(path, drops)) == []
    assert drops["no_model"] == 1


def test_malformed_lines_are_skipped_and_counted(tmp_path):
    path = tmp_path / "rollout-y.jsonl"
    path.write_text("{not json\n[]\n", encoding="utf-8")
    drops: Counter = Counter()
    assert list(codex.parse(path, drops)) == []
    assert drops["bad_json"] == 1 and drops["not_an_object"] == 1


def test_a_resumed_session_reemitting_an_old_total_keeps_the_original_model(
    codex_home,
):
    # Seen in real logs: a session resumed days later on another model repeated
    # an earlier gpt-5.5 total right after its new turn_context.
    lines = codex_home.read_text(encoding="utf-8").splitlines()
    replay = [
        (
            '{"timestamp":"2026-09-13T00:00:00.000Z","type":"turn_context",'
            '"payload":{"model":"gpt-5.4","cwd":"/home/dev/demo"}}'
        ),
        lines[-1].replace("2026-09-10T01:07:00.000Z", "2026-09-13T00:00:01.000Z"),
    ]
    codex_home.write_text("\n".join(lines + replay) + "\n", encoding="utf-8")
    kept, dropped = dedupe(codex.parse(codex_home))
    assert len(kept) == 3 and dropped == 2
    assert "gpt-5.4" not in {t.model for t in kept}
