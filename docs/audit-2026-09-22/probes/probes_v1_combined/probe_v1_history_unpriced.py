from __future__ import annotations
import os
from tests.probes_d1.d1util import JST, projects_root, rec, write_jsonl


def test_restored_day_keeps_unpriced(tmp_path):
    from ccburn import pricing
    from ccburn.cli import build_report
    pricing._remote = None
    root = projects_root(tmp_path)
    f = write_jsonl(root / "p" / "a.jsonl", [
        rec("m1", "r1", ts="2026-08-01T03:00:00Z", model="claude-haiku-9", inp=1_000_000, out=0),
    ])
    cfg = {"offline": True, "quota": False, "history": True}
    live, *_ = build_report(cfg, JST)
    os.remove(f)
    rest, *_ = build_report(cfg, JST)
    print("LIVE", live.total.unpriced, live.total.cost, live.unknown_models,
          "RESTORED", rest.total.unpriced, rest.total.cost, rest.unknown_models, rest.restored_days,
          "input", rest.total.input)
    assert rest.restored_days
    assert rest.total.unpriced == 1
