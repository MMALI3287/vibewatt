"""d5 probes: progress concierge (PLAN 7.13)."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from vibewatt import concierge, config, store
from vibewatt.api import create_app


def snapshot(root):
    out = {}
    for p in root.rglob("*"):
        st = p.lstat()
        out[str(p.relative_to(root))] = (
            p.is_dir(), st.st_mtime_ns, st.st_size, None if p.is_dir() else p.read_bytes()
        )
    return out


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "a@b.c")
    git(root, "config", "user.name", "a")
    (root / "tracked.txt").write_text("v1\n")
    (root / "TODO.md").write_text("- [ ] first open item\n* [x] done item\n+ [ ] second open item\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    # stat-dirty but content-identical file: a plain `git status` would refresh and rewrite the index
    time.sleep(1.1)
    (root / "tracked.txt").write_text("v1\n")
    (root / "new.txt").write_text("untracked\n")
    return root


def write_sessions(tmp_path):
    lines = []
    for sid, ts, title in (("old", "2026-09-01T00:00:00Z", "OLD TITLE"), ("new", "2026-09-10T00:00:00Z", "NEW TITLE")):
        lines.append({"type": "assistant", "requestId": "r" + sid, "timestamp": ts, "sessionId": sid,
                      "cwd": "/work/proj", "message": {"id": "m" + sid, "model": "claude-opus-5",
                      "usage": {"input_tokens": 1, "output_tokens": 1}}})
        lines.append({"type": "last-prompt", "sessionId": sid, "timestamp": ts, "lastPrompt": title})
    p = tmp_path / "claude" / "projects" / "proj" / "s.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_brief_contents_and_read_only(tmp_path):
    root = make_repo(tmp_path)
    write_sessions(tmp_path)
    c = TestClient(create_app({**config.DEFAULTS, "offline": True, "quota": False, "timezone": "utc",
                               "project_paths": {"proj": str(root)}}))
    c.post("/api/sync")
    data_dir = tmp_path / "data"
    before, before_data = snapshot(root), snapshot(data_dir)
    r = c.get("/api/concierge", params={"project": "proj"})
    after, after_data = snapshot(root), snapshot(data_dir)
    body = r.json()
    print("BRIEF\n" + body["text"], body["notes"])
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    print("CHANGED in project", changed)
    print("CHANGED in data dir", [k for k in set(before_data) | set(after_data) if before_data.get(k) != after_data.get(k)])
    assert "Last session: NEW TITLE" in body["text"]
    assert "first open item" in body["text"] and "second open item" in body["text"]
    assert "done item" not in body["text"]
    assert "?? new.txt" in body["text"]
    assert not (root / ".git" / "index.lock").exists()
    assert changed == []


def test_brief_is_read_only_direct_call(tmp_path):
    """Same, without the git-status call in the previous test's arrangement."""
    root = make_repo(tmp_path)
    before = snapshot(root)
    with store.connect(tmp_path / "x.db") as conn:
        result = concierge.build(conn, {"project_paths": {"p": str(root)}}, "p")
    after = snapshot(root)
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    print("DIRECT changed", changed, result["notes"])
    assert changed == []


@pytest.mark.parametrize("name", ["../../etc", "..\\..\\Windows", "/etc/passwd", "C:\\Windows"])
def test_traversal_names_are_only_keys(tmp_path, name):
    with store.connect(tmp_path / "x.db") as conn:
        r = concierge.build(conn, {"project_paths": {}}, name)
    assert "Project directory unavailable" in r["text"] and "Uncommitted" not in r["text"]


def test_relative_mapping_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_repo(tmp_path)
    with store.connect(tmp_path / "x.db") as conn:
        r = concierge.build(conn, {"project_paths": {"p": "proj"}}, "p")
    assert "Project directory unavailable" in r["text"]


def test_symlinked_todo_rejected(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    secret = tmp_path / "outside.md"
    secret.write_text("- [ ] OUTSIDE SECRET\n")
    try:
        os.symlink(secret, root / "TODO.md")
    except OSError as exc:
        pytest.skip(f"symlink not permitted: {exc}")
    with store.connect(tmp_path / "x.db") as conn:
        r = concierge.build(conn, {"project_paths": {"p": str(root)}}, "p")
    assert "OUTSIDE SECRET" not in r["text"]


def test_todo_size_boundary(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    line = b"- [ ] item\n"
    data = line * (65536 // len(line))
    data += b"x" * (65536 - len(data))
    (root / "TODO.md").write_bytes(data)
    with store.connect(tmp_path / "x.db") as conn:
        ok = concierge.build(conn, {"project_paths": {"p": str(root)}}, "p")
        (root / "TODO.md").write_bytes(data + b"y")
        big = concierge.build(conn, {"project_paths": {"p": str(root)}}, "p")
    print("SIZE ok notes", ok["notes"], "big notes", big["notes"])
    assert "Unfinished todos:" in ok["text"] and "Showing the first 50 unfinished todos." in ok["notes"]
    assert any("64 KiB" in n for n in big["notes"])


def test_non_git_dir_and_git_missing(tmp_path, monkeypatch):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "TODO.md").write_text("- [ ] open\n")
    with store.connect(tmp_path / "x.db") as conn:
        r = concierge.build(conn, {"project_paths": {"p": str(root)}}, "p")
        print("NONGIT", r["notes"])
        assert "Git status unavailable." in r["notes"] and "open" in r["text"]
        repo = make_repo(tmp_path)
        monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
        r2 = concierge.build(conn, {"project_paths": {"p": str(repo)}}, "p")
    print("NOGIT", r2["notes"])
    assert "Git status unavailable." in r2["notes"]
