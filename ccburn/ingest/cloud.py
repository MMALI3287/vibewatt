from __future__ import annotations


def discover(cfg: dict | None = None) -> list[dict]:
    """Cloud sessions are not local files; discovery is a no-op here."""
    return []


def parse(payload) -> list[dict]:
    """Return the session payloads in the cloud listing format we already ingest."""
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]
