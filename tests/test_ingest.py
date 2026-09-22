from __future__ import annotations

import inspect

from vibewatt import ingest
from vibewatt.ingest import cloud
from vibewatt.sources import CLAUDE_CODE, COWORK


def test_discover_finds_every_local_source(logs):
    found = set(ingest.discover())
    assert (CLAUDE_CODE, logs["claude-code"]) in found
    assert (COWORK, logs["cowork"]) in found


def test_discover_ignores_files_outside_source_roots(tmp_path, logs):
    stray = tmp_path / "claude" / "not-projects" / "x.jsonl"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}\n", encoding="utf-8")
    assert stray not in {p for _, p in ingest.discover()}


def test_every_file_source_has_the_same_interface():
    for mod in ingest.SOURCES.values():
        assert list(inspect.signature(mod.discover).parameters) == ["cfg"]
        assert list(inspect.signature(mod.parse).parameters) == ["path", "drops", "raw"]


def test_parse_does_not_dedup_within_a_file(logs):
    # Dedup belongs to the sync, across files. A parser that deduped per file
    # would hide the repeats the sync has to collapse and count.
    turns = list(ingest.parse(CLAUDE_CODE, logs["claude-code"]))
    assert [t.key for t in turns] == [("m1", "r1"), ("m1", "r1"), ("m2", "r2")]


def test_cowork_envelope_is_normalised(logs):
    (turn,) = ingest.parse(COWORK, logs["cowork"])
    assert turn.session == "c1"
    assert turn.source == COWORK
    assert turn.input == 30


def test_cloud_parse_accepts_both_listing_shapes():
    rows = [{"id": "a"}, "junk"]
    assert cloud.parse(rows) == [{"id": "a"}]
    assert cloud.parse({"data": rows}) == [{"id": "a"}]
    assert cloud.parse({"nope": 1}) == []
