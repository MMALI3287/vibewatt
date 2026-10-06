from __future__ import annotations

from pathlib import Path

from vibewatt.sources import CLAUDE_CODE, dedupe, read_file

FIXTURE = Path(__file__).parent / "fixtures" / "sanitized_provider_example.jsonl"


def test_sanitized_provider_example_parses_and_preserves_dedup_relationships():
    parsed = list(read_file(CLAUDE_CODE, FIXTURE))
    turns, dropped = dedupe(parsed)

    assert len(parsed) == 3
    assert len(turns) == 2
    assert dropped == 1
    assert sum(turn.input for turn in turns) == 15
    assert sum(turn.output for turn in turns) == 12
    assert {turn.key for turn in turns} == {
        ("fixture-message-1", "fixture-request-1"),
        ("fixture-message-2", "fixture-request-2"),
    }
