"""Reject release artifacts without the separately built React dashboard."""

from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        # Editable installs support Vite development before a production build.
        if version == "editable":
            return
        static = Path(self.root) / "vibewatt" / "static"
        if not (static / "index.html").is_file() or not list(
            (static / "assets").glob("*.js")
        ):
            raise RuntimeError(
                "React build missing. Run npm ci and npm run build in web/ "
                "before building the vibewatt distribution."
            )
