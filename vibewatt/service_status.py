"""Claude's public service status, cached and bounded.

Reads https://status.claude.com/api/v2/summary.json. The whole lookup has a
3-second deadline (A-095): urlopen's timeout is per socket read, so a server
that drips bytes could hold a request far longer. The fetch runs in a worker
thread and is abandoned at the deadline; the lock is never held across I/O.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.request import urlopen

URL = "https://status.claude.com"
SUMMARY = f"{URL}/api/v2/summary.json"
DEADLINE_SECONDS = 3.0
MAX_BYTES = 512 * 1024
CACHE_SECONDS = 300
_INDICATORS = {"none", "minor", "major", "critical"}

_expires = 0.0
_cached: dict | None = None
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vibewatt-status")


def _fetch() -> dict | None:
    with urlopen(SUMMARY, timeout=DEADLINE_SECONDS) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("oversized response")
    status = json.loads(raw)["status"]
    indicator, description = status["indicator"], status["description"]
    if indicator not in _INDICATORS or not isinstance(description, str):
        raise ValueError("invalid status")
    return {"indicator": indicator, "description": description[:500], "url": URL}


def read() -> dict | None:
    global _expires, _cached
    with _lock:
        if time.monotonic() < _expires:
            return dict(_cached) if _cached else None
    try:
        fresh = _pool.submit(_fetch).result(timeout=DEADLINE_SECONDS)
    except (FutureTimeout, OSError, ValueError, KeyError, TypeError):
        fresh = None
    with _lock:
        _cached = fresh
        _expires = time.monotonic() + CACHE_SECONDS
        return dict(_cached) if _cached else None
