from __future__ import annotations

import inspect
import runpy
from pathlib import Path

import pytest

from vibewatt import ingest
from vibewatt.ingest import cloud
from vibewatt.sources import CLAUDE_CODE, COWORK


def test_discover_finds_every_local_source(logs):
    found = set(ingest.discover())
    assert (CLAUDE_CODE, logs["claude-code"]) in found
    assert (COWORK, logs["cowork"]) in found


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_fixture_sources_are_discovered_on_every_platform(logs, monkeypatch, system):
    monkeypatch.setattr("vibewatt.ingest.cowork.platform.system", lambda: system)
    files = ingest.discover()
    assert len(files) == 2
    assert set(files) == {
        (CLAUDE_CODE, logs["claude-code"]),
        (COWORK, logs["cowork"]),
    }


def test_e2e_fixture_includes_cowork_on_darwin(tmp_path, monkeypatch):
    import uvicorn

    from vibewatt import store

    monkeypatch.setattr("vibewatt.ingest.cowork.platform.system", lambda: "Darwin")
    monkeypatch.setattr("tempfile.mkdtemp", lambda **kwargs: str(tmp_path / "e2e"))
    monkeypatch.setattr("sys.argv", ["fixture_server.py"])
    for key in (
        "CLAUDE_CONFIG_DIR",
        "APPDATA",
        "XDG_CONFIG_HOME",
        "VIBEWATT_DATA_DIR",
        "VIBEWATT_COWORK_DIR",
        "HOME",
        "USERPROFILE",
    ):
        monkeypatch.setenv(key, str(tmp_path / "isolated"))

    def inspect_store(app, **kwargs):
        with store.connect() as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM turns WHERE source = 'cowork'"
                ).fetchone()[0]
                == 1
            )

    monkeypatch.setattr(uvicorn, "run", inspect_store)
    server = Path(__file__).parents[1] / "web/e2e/fixture_server.py"
    runpy.run_path(str(server))["main"]()


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
