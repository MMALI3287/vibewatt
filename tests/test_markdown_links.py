from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_markdown_links.py"
FIXTURES = ROOT / "tests" / "fixtures" / "markdown_links"


def _load_checker() -> ModuleType:
    """Import the standalone script as a module for direct unit tests."""
    spec = importlib.util.spec_from_file_location("check_markdown_links", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), *[str(p) for p in paths]],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_fixture_passes():
    result = _run(FIXTURES / "valid")
    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout


def test_broken_fixture_fails():
    result = _run(FIXTURES / "broken")
    assert result.returncode == 1
    err = result.stderr
    assert "missing path" in err or "missing anchor" in err


def test_github_slug_does_not_collapse_repeated_hyphens():
    checker = _load_checker()
    # GitHub drops "&" without substituting anything, so the two spaces
    # around it survive as two hyphens: "a--b", not "a-b".
    assert checker.github_slug("A & B") == "a--b"


def test_github_slug_keeps_underscores():
    checker = _load_checker()
    assert checker.github_slug("Set `api_key` first") == "set-api_key-first"


def test_gather_headings_resolves_collision_chain(tmp_path: Path):
    checker = _load_checker()
    doc = tmp_path / "doc.md"
    doc.write_text("# Foo\n\n# Foo\n\n# Foo-1\n", encoding="utf-8")
    # The third heading's own slug ("foo-1") collides with the slug GitHub
    # already assigned the second "Foo", so it gets bumped again.
    assert checker.gather_headings(doc) == {"foo", "foo-1", "foo-1-1"}


def test_heading_collision_fixture_passes():
    result = _run(FIXTURES / "valid" / "headings.md")
    assert result.returncode == 0, result.stderr


def test_heading_collision_fixture_rejects_collapsed_hyphen():
    result = _run(FIXTURES / "broken" / "headings.md")
    assert result.returncode == 1
    assert "missing anchor #a-b" in result.stderr


def test_reference_style_links_resolve():
    result = _run(FIXTURES / "valid" / "refs.md")
    assert result.returncode == 0, result.stderr


def test_reference_style_links_report_missing_target():
    result = _run(FIXTURES / "broken" / "refs.md")
    assert result.returncode == 1
    assert "missing path" in result.stderr
