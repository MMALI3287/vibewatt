"""Select CI checks from the complete change set without skipping required checks."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

SCOPES = ("python", "web", "api", "package", "deps", "audit")


def classify(paths: list[str], *, full: bool = False) -> dict[str, bool]:
    scopes = dict.fromkeys(SCOPES, full)
    for path in paths:
        if path in {"pyproject.toml", "uv.lock"} or path in {
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            "scripts/ci_scope.py",
        }:
            scopes.update(dict.fromkeys(SCOPES, True))
        elif path == "docs/PLAN.md":
            # The Wrapped contract test reads this document.
            scopes["python"] = True
        elif (
            path.startswith(("docs/", ".github/ISSUE_TEMPLATE/"))
            or path.endswith(".md")
            or path in {"LICENSE", ".github/dependabot.yml"}
        ):
            continue
        elif path.startswith("web/"):
            scopes["web"] = True
            if path in {"web/openapi.json", "web/src/api/schema.d.ts"}:
                scopes["api"] = True
            if path in {"web/package.json", "web/package-lock.json"}:
                scopes["audit"] = True
        elif path.startswith("vibewatt/"):
            scopes["python"] = True
            # Backend changes can alter every browser route through the API.
            scopes["web"] = True
            if path.startswith("vibewatt/api/"):
                scopes["api"] = True
        elif path.startswith("tests/"):
            scopes["python"] = True
        elif path == "scripts/dump_openapi.py":
            scopes["python"] = scopes["web"] = scopes["api"] = True
        else:
            # Unknown build inputs must get checked until explicitly classified.
            scopes.update(dict.fromkeys(SCOPES, True))
    return scopes


def changed_paths(base: str) -> list[str] | None:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base) or base == "0" * 40:
        return None
    try:
        if subprocess.run(
            ["git", "cat-file", "-e", f"{base}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode:
            subprocess.run(
                ["git", "fetch", "--no-tags", "--depth=1", "origin", base],
                check=True,
                timeout=60,
            )
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD"],
            capture_output=True,
            check=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        # A missing comparison must never produce a false documentation-only pass.
        return None
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main() -> None:
    full = os.environ.get("FULL", "false").lower() == "true"
    paths = None if full else changed_paths(os.environ.get("BASE", ""))
    scopes = classify(paths or [], full=full or paths is None)
    lines = [f"{name}={str(enabled).lower()}" for name, enabled in scopes.items()]
    print("\n".join(lines))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
