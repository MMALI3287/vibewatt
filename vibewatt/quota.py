"""Account-level plan utilization.

Local session logs only ever describe the machine they are on. Claude Code on
the web and Cowork remote sessions run in throwaway cloud containers whose logs
are destroyed with the container, so they can never appear in a local file.

The plan-utilization endpoint is the one source that does account for them: it
reports how much of your 5-hour and 7-day allowance is spent across everything
attached to the account, whichever surface produced it. It is a percentage, not
a token ledger, so it complements the local history rather than replacing it.

The OAuth token is read with the same lookup order Claude Code itself uses. It
is sent only to Anthropic's own endpoint and is never logged or persisted.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "vibewatt"


@dataclass
class Window:
    label: str
    utilization: float          # percent, 0-100
    resets_at: datetime | None

    @property
    def remaining_seconds(self) -> float | None:
        if self.resets_at is None:
            return None
        return max(0.0, (self.resets_at - datetime.now(timezone.utc)).total_seconds())


@dataclass
class Quota:
    windows: list[Window]
    source: str                 # "endpoint" or "statusline"
    fetched_at: datetime

    def by_label(self, label: str) -> Window | None:
        for w in self.windows:
            if w.label == label:
                return w
        return None


def _credentials_path() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        first = configured.split(os.pathsep)[0]
        return Path(first).expanduser() / ".credentials.json"
    return Path.home() / ".claude" / ".credentials.json"


def _token_from_keychain() -> str | None:
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return _token_from_blob(out.stdout)


def _token_from_blob(raw: str) -> str | None:
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(blob, dict):
        return None
    for key in ("claudeAiOauth", "oauth", "credentials"):
        inner = blob.get(key)
        if isinstance(inner, dict):
            blob = inner
            break
    for key in ("accessToken", "access_token", "token"):
        value = blob.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def read_token() -> str | None:
    """Same lookup order Claude Code uses. Returns None when not signed in."""
    env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env:
        return env
    path = _credentials_path()
    try:
        token = _token_from_blob(path.read_text(encoding="utf-8"))
        if token:
            return token
    except OSError:
        pass
    return _token_from_keychain()


def _parse_reset(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):           # unix seconds
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


_LABELS = {
    "five_hour": "5-hour",
    "seven_day": "7-day",
    "five_hour_opus": "5-hour (Opus)",
    "seven_day_opus": "7-day (Opus)",
}


def _windows_from(payload: dict) -> list[Window]:
    windows: list[Window] = []
    for key, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        raw = entry.get("utilization", entry.get("used_percentage"))
        if raw is None:
            continue
        try:
            pct = float(raw)
        except (TypeError, ValueError):
            continue
        label = _LABELS.get(key, key.replace("_", " ").strip().title())
        windows.append(Window(label, pct, _parse_reset(entry.get("resets_at"))))
    order = {v: i for i, v in enumerate(_LABELS.values())}
    windows.sort(key=lambda w: order.get(w.label, 99))
    return windows


def from_statusline(path: str | Path) -> Quota | None:
    """Read a rate-limit dump written by a Claude Code statusLine hook.

    Free and seconds-fresh while a session is running, because Claude Code
    rewrites the statusline continuously. Costs no API call.
    """
    try:
        with Path(path).expanduser().open("r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    limits = blob.get("rate_limits") if isinstance(blob, dict) else None
    if not isinstance(limits, dict):
        return None
    windows = _windows_from(limits)
    if not windows:
        return None
    # Without captured_at, the file's mtime is the reading's time. now() would
    # turn one stale dump into a new sample on every read.
    captured = _parse_reset(blob.get("captured_at"))
    if captured is None:
        try:
            captured = datetime.fromtimestamp(Path(path).expanduser().stat().st_mtime, timezone.utc)
        except OSError:
            captured = datetime.now(timezone.utc)
    # A stale dump can describe a window that has since rolled over.
    now = datetime.now(timezone.utc)
    for w in windows:
        if w.resets_at is not None and w.resets_at < now:
            w.utilization = 0.0
    return Quota(windows, "statusline", captured)


def fetch(token: str | None = None, timeout: float = 10.0) -> Quota | None:
    """Ask the account-level endpoint. Returns None if signed out or unreachable."""
    token = token or read_token()
    if not token:
        return None
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    windows = _windows_from(payload)
    if not windows:
        return None
    return Quota(windows, "endpoint", datetime.now(timezone.utc))


def _record(quota: Quota) -> None:
    """Append to the sample series. A store failure must never cost the reading."""
    import sqlite3

    from . import store

    try:
        with store.connect() as conn:
            store.upsert_quota_samples(conn, quota)
    except (sqlite3.Error, OSError):
        pass


def read(config: dict) -> tuple[Quota | None, str]:
    """Best available quota reading, plus a human explanation when there is none."""
    if not config.get("quota", True):
        return None, "disabled in config"
    cached = config.get("statusline_cache_path")
    if cached:
        quota = from_statusline(cached)
        if quota is not None:
            _record(quota)
            return quota, ""
    if not read_token():
        return None, "not signed in (no Claude Code OAuth token found)"
    quota = fetch()
    if quota is None:
        return None, "endpoint unreachable or returned nothing"
    _record(quota)
    return quota, ""
