"""d6 probes: concierge path handling."""
from __future__ import annotations

import subprocess

from ccburn import concierge, store


def test_concierge_only_uses_configured_absolute_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "new.txt").write_text("x")
    (repo / "TODO.md").write_text("- [ ] ship it\n- [x] done\n")
    outside = tmp_path / "secret.txt"
    outside.write_text("- [ ] SECRET\n")
    cfg = {"project_paths": {"demo": str(repo), "rel": "repo", "up": str(repo / ".." / "repo")}}
    with store.connect() as conn:
        ok = concierge.build(conn, cfg, "demo")
        trav = concierge.build(conn, cfg, "../../" + str(outside))
        rel = concierge.build(conn, cfg, "rel")
    print(ok["text"])
    print("traversal notes:", trav["notes"], "| relative notes:", rel["notes"])
    assert "?? new.txt" in ok["text"] and "- [ ] ship it" in ok["text"] and "done" not in ok["text"]
    assert "SECRET" not in trav["text"] and trav["notes"]
    assert rel["notes"] and "Configure an absolute directory" in rel["notes"][0]
