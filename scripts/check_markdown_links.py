#!/usr/bin/env python3
"""Offline checker for repository-relative Markdown links and heading anchors.

Supported:
  - [text](relative/path.md)
  - [text](relative/path.md#Heading-Anchor)
  - ![alt](images/foo.png)
  - bare directory links that resolve to an existing path

Ignored (no network):
  - http(s)://, mailto:, irc:, data:
  - absolute site-root paths starting with / that are not repo-relative
  - reference-style definitions are resolved when present in the same file

Fenced code blocks and inline ``code`` spans are skipped so example URLs do not
fail the check. Heading anchors follow GitHub's slug rules (lowercase, spaces to
hyphens, strip punctuation; duplicate headings get -1, -2, ...).
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"`[^`]+`")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
REF_DEF_RE = re.compile(r"^\[([^\]]+)\]:\s*(\S+)")


def github_slug(text: str) -> str:
    text = text.strip().lower()
    out = []
    for ch in text:
        if ch.isalnum() or ch in "- ":
            out.append("-" if ch == " " else ch)
        # drop other punctuation
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def strip_inline_code(line: str) -> str:
    return INLINE_CODE_RE.sub("", line)


def gather_headings(path: Path) -> set[str]:
    counts: dict[str, int] = defaultdict(int)
    slugs: set[str] = set()
    in_fence = False
    fence_marker = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        fence = FENCE_RE.match(raw)
        if fence:
            marker = fence.group(1)[0] * len(fence.group(1))
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif raw.startswith(fence_marker[0] * len(fence_marker)):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(raw)
        if not m:
            continue
        base = github_slug(m.group(2))
        n = counts[base]
        counts[base] = n + 1
        slugs.add(base if n == 0 else f"{base}-{n}")
    return slugs


def iter_markdown_files(root: Path, *, skip_broken_fixtures: bool = False) -> list[Path]:
    skip = {".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".tox"}
    files: list[Path] = []
    for path in root.rglob("*.md"):
        if any(part in skip for part in path.parts):
            continue
        # Intentional negative fixtures for the checker itself.
        if skip_broken_fixtures and "fixtures/markdown_links/broken" in path.as_posix():
            continue
        files.append(path)
    return sorted(files)


def check_file(path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    headings_cache: dict[Path, set[str]] = {}
    in_fence = False
    fence_marker = ""
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fence = FENCE_RE.match(raw)
        if fence:
            marker = fence.group(1)[0] * len(fence.group(1))
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif raw.startswith(fence_marker[0] * len(fence_marker)):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        line = strip_inline_code(raw)
        for match in LINK_RE.finditer(line):
            target = match.group(1).strip()
            if not target or target.startswith("#"):
                # same-file anchor
                if target.startswith("#"):
                    dest = path
                    frag = urllib.parse.unquote(target[1:])
                    if dest not in headings_cache:
                        headings_cache[dest] = gather_headings(dest)
                    if frag and github_slug(frag) not in headings_cache[dest] and frag not in headings_cache[dest]:
                        # try raw and slug forms
                        slug = github_slug(frag)
                        if slug not in headings_cache[dest]:
                            errors.append(f"{path.relative_to(root)}:{lineno}: missing anchor #{frag}")
                continue
            parsed = urllib.parse.urlparse(target)
            if parsed.scheme in {"http", "https", "mailto", "irc", "data"}:
                continue
            if target.startswith("//"):
                continue
            # split fragment
            file_part, _, frag = target.partition("#")
            file_part = urllib.parse.unquote(file_part)
            if not file_part:
                dest = path
            else:
                dest = (path.parent / file_part).resolve()
                try:
                    dest.relative_to(root.resolve())
                except ValueError:
                    errors.append(f"{path.relative_to(root)}:{lineno}: link escapes repository: {target}")
                    continue
                if not dest.exists():
                    errors.append(f"{path.relative_to(root)}:{lineno}: missing path {file_part}")
                    continue
            if frag:
                if dest.is_dir():
                    errors.append(f"{path.relative_to(root)}:{lineno}: anchor on directory {target}")
                    continue
                if dest.suffix.lower() != ".md":
                    continue
                if dest not in headings_cache:
                    headings_cache[dest] = gather_headings(dest)
                slug = github_slug(urllib.parse.unquote(frag))
                if slug not in headings_cache[dest] and urllib.parse.unquote(frag) not in headings_cache[dest]:
                    errors.append(f"{path.relative_to(root)}:{lineno}: missing anchor #{frag} in {dest.relative_to(root)}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Markdown files or directories (default: repository root)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root for relative reporting (default: cwd)",
    )
    args = parser.parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    if args.paths:
        files: list[Path] = []
        for p in args.paths:
            p = p.resolve()
            if p.is_dir():
                files.extend(iter_markdown_files(p, skip_broken_fixtures=False))
            else:
                files.append(p)
    else:
        files = iter_markdown_files(root, skip_broken_fixtures=True)
    all_errors: list[str] = []
    for path in files:
        all_errors.extend(check_file(path, root))
    if all_errors:
        print("Markdown link check failed:", file=sys.stderr)
        for err in all_errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    print(f"OK: checked {len(files)} Markdown file(s), no broken local links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
