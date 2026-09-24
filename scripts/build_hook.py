"""Reject release artifacts without the separately built React dashboard."""

from __future__ import annotations

import re
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        # Editable installs support Vite development before a production build.
        if version == "editable":
            return
        static = Path(self.root) / "vibewatt" / "static"
        index = static / "index.html"
        assets = static / "assets"
        hashed = re.compile(r".+-[A-Za-z0-9_-]{6,}\.(js|css)$")
        files = [p for p in assets.glob("*") if hashed.fullmatch(p.name)]
        if not index.is_file() or not all(
            any(p.suffix == ext for p in files) for ext in (".js", ".css")
        ):
            raise RuntimeError(
                "React build missing or incomplete. Run npm ci and npm run build in web/ "
                "before building the vibewatt distribution."
            )
        for name in re.findall(
            r'(?:src|href)="(/assets/[^"?]+)', index.read_text(encoding="utf-8")
        ):
            if not (static / name.lstrip("/")).is_file():
                raise RuntimeError(f"React build references missing asset: {name}")
