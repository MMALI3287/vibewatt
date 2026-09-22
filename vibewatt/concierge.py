"""A read-only, copyable project resume brief."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import threading
from pathlib import Path

from . import store


def _git_status(root: Path) -> str:
    output = []
    with subprocess.Popen(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ],
        cwd=root,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ) as process:

        def collect():
            data = process.stdout.read(65537)
            output.append(data)
            if len(data) > 65536:
                process.kill()

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            reader.join(timeout=1)
        if process.returncode or not output or len(output[0]) > 65536:
            raise ValueError("Git status unavailable or too large")
    return output[0].decode("utf-8", errors="replace").strip()


def build(conn: sqlite3.Connection, cfg: dict, project: str) -> dict:
    notes = []
    sessions = store.sessions(conn, project=project, limit=1)
    title = sessions[0]["title"] if sessions else "No recorded session"
    lines = [f"Resume project: {project}", f"Last session: {title}"]
    mapping = cfg.get("project_paths") or {}
    configured = mapping.get(project) if isinstance(mapping, dict) else None
    root = Path(configured).expanduser() if isinstance(configured, str) else None
    if root is None or not root.is_absolute() or not root.is_dir():
        notes.append(
            "Project directory unavailable. Configure an absolute directory in project_paths."
        )
    else:
        root = root.resolve()
        try:
            status = _git_status(root)
            lines.extend(["Uncommitted changes:", status or "Working tree is clean."])
        except (OSError, ValueError, subprocess.TimeoutExpired):
            notes.append("Git status unavailable.")
        todo = root / "TODO.md"
        try:
            if todo.is_symlink() or todo.resolve().parent != root:
                raise ValueError("TODO.md must stay inside the project directory")
            with todo.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("TODO.md exceeds 64 KiB")
            unfinished = [
                line.strip()
                for line in raw.decode("utf-8", errors="replace").splitlines()
                if re.match(r"^\s*[-*+]\s+\[ \]\s+", line)
            ]
            lines.extend(
                [
                    "Unfinished todos:",
                    "\n".join(unfinished[:50]) or "No unfinished checkboxes.",
                ]
            )
            if len(unfinished) > 50:
                notes.append("Showing the first 50 unfinished todos.")
        except (OSError, ValueError):
            notes.append("TODO.md unavailable, unsafe or larger than 64 KiB.")
    lines.extend(notes)
    return {"project": project, "text": "\n".join(lines), "notes": notes}
