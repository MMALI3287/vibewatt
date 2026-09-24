"""Verify release guards using disposable copies of the built source archive."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sdist", type=Path)
    args = parser.parse_args()
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required on the build host")
    with tempfile.TemporaryDirectory(prefix="vibewatt-build-guard-") as temporary:
        with tarfile.open(args.sdist) as archive:
            archive.extractall(temporary, filter="data")
        root = next(Path(temporary).iterdir())
        assets = root / "vibewatt/static/assets"
        sentinel = assets / "excluded-12345678.js.map"
        sentinel.write_text("source maps must not ship", encoding="utf-8")
        for mode in ("complete", "missing-css", "missing-index"):
            if mode == "missing-css":
                # Disposable source copy only: prove the release refuses an incomplete build.
                for css in assets.glob("*.css"):
                    css.unlink()
            if mode == "missing-index":
                (root / "vibewatt/static/index.html").unlink()
            result = subprocess.run(
                [uv, "build", "--offline", "--wheel", str(root)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            if mode == "complete":
                assert result.returncode == 0, result.stderr
                wheel = next((root / "dist").glob("*.whl"))
                with zipfile.ZipFile(wheel) as archive:
                    assert not any(name.endswith(".map") for name in archive.namelist())
            else:
                assert result.returncode != 0, mode
                assert "React build missing or incomplete" in result.stderr
            print(f"PASS release guard: {mode}", flush=True)


if __name__ == "__main__":
    main()
