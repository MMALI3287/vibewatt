"""Cloud sessions (Claude Code on the web, Cowork remote).

These never touch local disk, so there is nothing to discover. Input is a
session listing captured with ``vibewatt harvest --file``.
"""

from __future__ import annotations


def parse(payload: object) -> list[dict]:
    """Normalise a listing (bare list or ``{"data": [...]}``) to session dicts."""
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]
