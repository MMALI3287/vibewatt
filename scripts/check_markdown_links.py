"""Offline checker for repository-relative Markdown links and heading anchors.

Supported:
  - [text](relative/path.md)
  - [text](relative/path.md#heading-anchor)
  - ![alt](images/foo.png)
  - bare directory links that resolve to an existing path
  - reference-style links, [text][label] and the collapsed [text][], resolved
    against a [label]: target definition in the same file

Ignored (no network):
  - http(s)://, mailto:, irc:, data:
  - absolute site-root paths starting with / that are not repo-relative
  - shortcut reference links ([label] with no second bracket pair)

Fenced code blocks and inline ``code`` spans are skipped so example URLs do
not fail the check. Heading anchors follow GitHub's slug rules: lowercase,
punctuation other than "-" and "_" is dropped rather than replaced (so
"A & B" becomes "a--b", not "a-b", and "api_key" stays "api_key"), and
remaining spaces become hyphens. Colliding slugs are resolved the
way GitHub's own slugger does it: a repeat gets "-N" appended to the base
slug, and if that candidate is itself already taken (for example a later
heading literally titled "Foo-1"), the counter keeps advancing until it
lands on a slug nothing else has claimed yet. A link's fragment is
percent-decoded and must equal one of those generated anchors exactly; it is
never slugified itself, so "#A-B" and "#a-b" do not match "a--b".
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"`[^`]+`")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REF_LINK_RE = re.compile(r"!?\[([^\]]*)\]\[([^\]]*)\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
REF_DEF_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(\S+)")


def github_slug(text: str) -> str:
    """Slugify heading text the way GitHub does: lowercase, drop punctuation
    other than "-" and "_" without substituting anything (so adjacent spaces
    survive as adjacent hyphens instead of collapsing), and turn remaining
    spaces into hyphens."""
    text = text.strip().lower()
    out = []
    for ch in text:
        if ch.isalnum() or ch in "-_ ":
            out.append("-" if ch == " " else ch)
        # other punctuation is dropped, not replaced with anything
    return "".join(out)


def strip_inline_code(line: str) -> str:
    return INLINE_CODE_RE.sub("", line)


def _unfenced_lines(text: str) -> list[tuple[int, str]]:
    """Return (1-based line number, line) pairs, skipping fenced code blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    fence_marker = ""
    for lineno, raw in enumerate(text.splitlines(), 1):
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
        out.append((lineno, raw))
    return out


def gather_headings(path: Path) -> set[str]:
    occurrences: dict[str, int] = {}
    slugs: set[str] = set()
    for _, raw in _unfenced_lines(path.read_text(encoding="utf-8")):
        m = HEADING_RE.match(raw)
        if not m:
            continue
        slug = github_slug(m.group(2))
        if slug in occurrences:
            base = slug
            while slug in occurrences:
                occurrences[base] += 1
                slug = f"{base}-{occurrences[base]}"
        occurrences[slug] = 0
        slugs.add(slug)
    return slugs


def gather_ref_defs(path: Path) -> dict[str, str]:
    """Map normalized reference labels to their targets (CommonMark link
    reference definitions: ``[label]: target``)."""
    defs: dict[str, str] = {}
    for _, raw in _unfenced_lines(path.read_text(encoding="utf-8")):
        m = REF_DEF_RE.match(raw)
        if m:
            defs[m.group(1).strip().casefold()] = m.group(2)
    return defs


def iter_markdown_files(
    root: Path, *, skip_broken_fixtures: bool = False
) -> list[Path]:
    skip = {
        ".git",
        "node_modules",
        ".venv",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".pytest_cache",
        ".ruff_cache",
    }
    files: list[Path] = []
    for path in root.rglob("*.md"):
        if any(part in skip for part in path.parts):
            continue
        # Intentional negative fixtures for the checker itself.
        if skip_broken_fixtures and "fixtures/markdown_links/broken" in path.as_posix():
            continue
        files.append(path)
    return sorted(files)


def _check_target(
    target: str,
    lineno: int,
    path: Path,
    root: Path,
    headings_cache: dict[Path, set[str]],
    errors: list[str],
) -> None:
    target = target.strip()
    if not target:
        return
    if target.startswith("#"):
        dest = path
        frag = urllib.parse.unquote(target[1:])
        if dest not in headings_cache:
            headings_cache[dest] = gather_headings(dest)
        if frag and frag not in headings_cache[dest]:
            errors.append(f"{path.relative_to(root)}:{lineno}: missing anchor #{frag}")
        return
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme in {"http", "https", "mailto", "irc", "data"}:
        return
    if target.startswith("//"):
        return
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
            errors.append(
                f"{path.relative_to(root)}:{lineno}: link escapes repository: {target}"
            )
            return
        if not dest.exists():
            errors.append(
                f"{path.relative_to(root)}:{lineno}: missing path {file_part}"
            )
            return
    if frag:
        if dest.is_dir():
            errors.append(
                f"{path.relative_to(root)}:{lineno}: anchor on directory {target}"
            )
            return
        if dest.suffix.lower() != ".md":
            return
        if dest not in headings_cache:
            headings_cache[dest] = gather_headings(dest)
        frag_decoded = urllib.parse.unquote(frag)
        if frag_decoded not in headings_cache[dest]:
            errors.append(
                f"{path.relative_to(root)}:{lineno}: missing anchor #{frag} in {dest.relative_to(root)}"
            )


def check_file(path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    headings_cache: dict[Path, set[str]] = {}
    text = path.read_text(encoding="utf-8")
    ref_defs = gather_ref_defs(path)
    for lineno, raw in _unfenced_lines(text):
        line = strip_inline_code(raw)
        for match in LINK_RE.finditer(line):
            _check_target(match.group(1), lineno, path, root, headings_cache, errors)
        for match in REF_LINK_RE.finditer(line):
            link_text, label = match.group(1), match.group(2)
            label = (label or link_text).strip().casefold()
            if label not in ref_defs:
                errors.append(
                    f"{path.relative_to(root)}:{lineno}: undefined reference link label [{label}]"
                )
                continue
            _check_target(ref_defs[label], lineno, path, root, headings_cache, errors)
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
