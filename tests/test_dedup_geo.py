from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC
from itertools import permutations

import pytest

from vibewatt import cli, store
from vibewatt.aggregate import cost_of
from vibewatt.ingest.claude_code import parse
from vibewatt.sources import dedupe


@pytest.mark.parametrize(
    "geos", [("global", "us"), (None, "us", "global"), ("global", "not_available")]
)
def test_geo_evidence_is_independent_of_duplicate_order(logs, geos):
    base = next(parse(logs["claude-code"]))
    expected = "us" if "us" in geos else "not_available"
    for order in permutations(geos):
        (turn,), _ = dedupe(replace(base, geo=geo) for geo in order)
        assert turn.geo == expected
        assert cost_of(turn) == pytest.approx(cost_of(replace(base, geo=expected)))


@pytest.mark.parametrize("geos", [("global", "us"), ("us", "global")])
def test_separate_syncs_keep_observed_geo_surcharge(tmp_path, logs, geos):
    base = next(parse(logs["claude-code"]))
    with store.connect(tmp_path / "db") as conn:
        for geo in geos:
            hours = store.upsert_turns(conn, [replace(base, geo=geo)], UTC, cost_of)
            store.rebuild_rollup(conn, hours)
        rows = conn.execute("SELECT geo,cost FROM turns").fetchall()
        assert len(rows) == 1
        assert rows[0]["geo"] == "us"
        expected = cost_of(replace(base, geo="us"))
        assert rows[0]["cost"] == pytest.approx(expected)
        assert conn.execute("SELECT SUM(cost) FROM rollup").fetchone()[
            0
        ] == pytest.approx(expected)


def test_unchanged_logs_repair_geo_from_an_older_checkpoint(logs):
    path = logs["claude-code"]
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    for record in records:
        if record.get("type") == "assistant":
            record["message"]["usage"]["inference_geo"] = "us"
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    cfg = {"offline": True}
    files = list(logs.items())
    cli.sync_store(cfg, UTC, files)
    with store.connect() as conn:
        conn.execute(
            "UPDATE turns SET geo='global',cost=cost/1.1 WHERE source='claude-code'"
        )
        hours = {
            row[0] for row in conn.execute("SELECT DISTINCT substr(ts,1,13) FROM turns")
        }
        store.rebuild_rollup(conn, hours)
        conn.execute("DELETE FROM meta WHERE key='dedupe_version'")
    cli.sync_store(cfg, UTC, files)
    with store.connect() as conn:
        assert {
            row[0]
            for row in conn.execute("SELECT geo FROM turns WHERE source='claude-code'")
        } == {"us"}
    assert cli.sync_store(cfg, UTC, files).parsed == 0
