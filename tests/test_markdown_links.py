from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_markdown_links.py"
FIXTURES = ROOT / "tests" / "fixtures" / "markdown_links"

# This module only shells out to the checker; skip heavy app autouse fixtures.
pytestmark = pytest.mark.usefixtures()


@pytest.fixture(autouse=True)
def _no_app_fixtures(monkeypatch):
    """Neutralize repo-wide autouse fixtures that need the full app stack."""
    return


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
