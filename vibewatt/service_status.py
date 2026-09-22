"""Small, failure-cached public status lookup."""

from __future__ import annotations

import json
import threading
import time
from urllib.request import urlopen

URL = "https://status.claude.com"
_expires = 0.0
_cached: dict | None = None
_lock = threading.Lock()


def read() -> dict | None:
    global _expires, _cached
    with _lock:
        if time.monotonic() < _expires:
            return dict(_cached) if _cached else None
        _cached = None
        try:
            with urlopen(f"{URL}/api/v2/status.json", timeout=3) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized response")
            status = json.loads(raw)["status"]
            indicator, description = status["indicator"], status["description"]
            if indicator not in {
                "none",
                "minor",
                "major",
                "critical",
            } or not isinstance(description, str):
                raise ValueError("invalid status")
            _cached = {
                "indicator": indicator,
                "description": description[:500],
                "url": URL,
            }
        except (OSError, ValueError, KeyError, TypeError):
            pass
        _expires = time.monotonic() + 300
        return dict(_cached) if _cached else None
