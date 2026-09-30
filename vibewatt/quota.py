"""Account-level plan utilization.

Local session logs only ever describe the machine they are on. Claude Code on
the web and Cowork remote sessions run in throwaway cloud containers whose logs
are destroyed with the container, so they can never appear in a local file.
Plan utilization is the one figure that covers every surface: how much of each
rate-limit window is spent across the whole account. It is a percentage, not a
token ledger, so it complements the local history rather than replacing it.

Sources, best first (docs/PLAN.md section 2.5, amended in Phase 6.5d):

  1. ``vibewatt statusline``: Claude Code pipes its documented statusline JSON
     (``rate_limits.<window>.used_percentage`` and ``resets_at`` in Unix
     seconds) to the command on every refresh. Free, live, no network.
  2. The Claude desktop app's ``plan-usage-history.json``, read only. It holds
     a sample every 15 minutes but no reset times.
  3. ``/api/oauth/usage``, the undocumented endpoint Claude Code calls. A
     fallback only: at most one call per 10 minutes, a backoff after 429, and
     never from a page request.

Every reading becomes a sample in ``quota_samples``. Readers take the newest
sample per window from the store, so every source feeds the same series.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "vibewatt"
FETCH_EVERY = timedelta(minutes=10)
FRESH = timedelta(minutes=10)  # a newer sample than this needs no fetch
STALE_DUMP = timedelta(minutes=10)  # an older statusline dump is not a sample (A-013)
STATUSLINE_THROTTLE = timedelta(seconds=60)
BACKOFF = (timedelta(minutes=10), timedelta(hours=6))
DESKTOP_VERSIONS = {2}

_LABELS = {
    "five_hour": "5-hour",
    "seven_day": "7-day",
    "five_hour_opus": "5-hour (Opus)",
    "seven_day_opus": "7-day (Opus)",
    "seven_day_sonnet": "7-day (Sonnet)",
    "spend_limit": "Spend limit",
}


def label_for(key: str) -> str:
    return _LABELS.get(key) or key.replace("_", " ").strip().capitalize()


def window_length(key: str) -> timedelta | None:
    """How long a window runs. Unknown for anything but the rolling limits."""
    if key.startswith("five_hour"):
        return timedelta(hours=5)
    if key.startswith("seven_day"):
        return timedelta(days=7)
    return None


@dataclass
class Window:
    key: str
    utilization: float  # percent, 0-100 (a spend limit can pass 100)
    resets_at: datetime | None
    scope: str = "account"  # the org for desktop samples
    source: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        self.label = self.label or label_for(self.key)

    @property
    def remaining_seconds(self) -> float | None:
        if self.resets_at is None:
            return None
        return max(0.0, (self.resets_at - datetime.now(UTC)).total_seconds())


@dataclass
class Quota:
    windows: list[Window]
    source: str  # statusline | desktop | endpoint | several
    fetched_at: datetime
    notes: list[str] = field(default_factory=list)

    def by_label(self, label: str) -> Window | None:
        for w in self.windows:
            if w.label == label:
                return w
        return None


# --- credentials ---------------------------------------------------------------


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
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
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


# --- parsing -------------------------------------------------------------------


def _parse_time(value) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value  # ms or s
        try:
            return datetime.fromtimestamp(float(seconds), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    return None


def windows_from(payload: dict, source: str, scope: str = "account") -> list[Window]:
    """Every rate-limit window in a payload, whatever it is called.

    A window is any object with a numeric ``utilization`` or
    ``used_percentage``. New windows appear without a code change.
    """
    windows: list[Window] = []
    for key, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        raw = entry.get("utilization", entry.get("used_percentage"))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        windows.append(
            Window(
                str(key), float(raw), _parse_time(entry.get("resets_at")), scope, source
            )
        )
    order = {k: i for i, k in enumerate(_LABELS)}
    windows.sort(key=lambda w: (order.get(w.key, 99), w.key))
    return windows


def from_statusline_json(blob: object, now: datetime | None = None) -> Quota | None:
    """Claude Code's statusline stdin: live, so it is always a sample."""
    now = now or datetime.now(UTC)
    limits = blob.get("rate_limits") if isinstance(blob, dict) else None
    if not isinstance(limits, dict):
        return None
    windows = [
        w
        for w in windows_from(limits, "statusline")
        if w.resets_at is None or w.resets_at > now
    ]
    return Quota(windows, "statusline", now) if windows else None


def from_statusline(path: str | Path, now: datetime | None = None) -> Quota | None:
    """A statusline JSON saved to disk by a hook (`statusline_cache_path`).

    A dump older than STALE_DUMP is shown nowhere and recorded nowhere: a
    saved reading re-read later is not a new sample (A-013). A window whose
    reset has passed is dropped, not rewritten to 0 %.
    """
    now = now or datetime.now(UTC)
    target = Path(path).expanduser()
    try:
        with target.open("r", encoding="utf-8") as fh:
            blob = json.load(fh)
        mtime = datetime.fromtimestamp(target.stat().st_mtime, UTC)
    except (OSError, json.JSONDecodeError):
        return None
    captured = _parse_time(blob.get("captured_at")) if isinstance(blob, dict) else None
    captured = captured or mtime
    if now - captured > STALE_DUMP:
        return None
    quota = from_statusline_json(blob, now)
    if quota is not None:
        quota.fetched_at = captured
    return quota


def desktop_history_path() -> Path | None:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        base = home / "Library" / "Application Support" / "Claude"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        base = Path(appdata) / "Claude"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "Claude"
    return base / "plan-usage-history.json"


_DESKTOP_KEYS = {"fh": "five_hour", "sd": "seven_day"}


def desktop_samples(path: Path | None = None) -> tuple[list[tuple], str]:
    """Rows from the desktop app's history, plus a note when it is skipped.

    Read only. Only a version this code was written against is imported: the file is
    an internal app cache whose shape can change without notice.
    """
    path = path or desktop_history_path()
    if path is None or not path.is_file():
        return [], ""
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "desktop plan history unreadable"
    if not isinstance(blob, dict) or blob.get("version") not in DESKTOP_VERSIONS:
        version = blob.get("version") if isinstance(blob, dict) else None
        return [], f"desktop plan history version {version!r} not supported"
    rows = []
    for sample in blob.get("samples") or []:
        if not isinstance(sample, dict) or not isinstance(sample.get("u"), dict):
            continue
        stamp = _parse_time(sample.get("t"))
        org = sample.get("org")
        if stamp is None or not isinstance(org, str) or not org:
            continue
        for short, key in _DESKTOP_KEYS.items():
            value = sample["u"].get(short)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            rows.append(
                (
                    stamp.isoformat(),
                    key,
                    label_for(key),
                    f"org:{org}",
                    float(value),
                    None,
                    "desktop",
                )
            )
    return rows, ""


# --- the endpoint ----------------------------------------------------------------


class RateLimited(Exception):
    pass


def fetch(token: str | None = None, timeout: float = 10.0) -> Quota | None:
    """Ask the account-level endpoint. None if signed out or unreachable.

    Raises RateLimited on HTTP 429 so the caller can back off.
    """
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
            raw = resp.read(64 * 1024 + 1)  # a few windows; never megabytes (A-102)
        if len(raw) > 64 * 1024:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited from exc
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    windows = windows_from(payload, "endpoint")
    return Quota(windows, "endpoint", datetime.now(UTC)) if windows else None


# --- the sample series ---------------------------------------------------------------


def record(conn, quota: Quota | None, *, throttle: timedelta | None = None) -> int:
    """Append one sample per window. Returns rows written.

    With `throttle`, a window whose last sample is newer than that and reads
    the same is skipped, so a statusline refreshing every second does not
    write a row a second.
    """
    if quota is None or not quota.windows:
        return 0
    ts = quota.fetched_at.astimezone(UTC)
    rows = []
    for w in quota.windows:
        if throttle is not None:
            last = conn.execute(
                "SELECT ts, utilization, resets_at FROM quota_samples"
                " WHERE key = ? AND scope = ? ORDER BY ts DESC LIMIT 1",
                (w.key, w.scope),
            ).fetchone()
            resets = w.resets_at.isoformat() if w.resets_at else None
            if (
                last
                and ts - datetime.fromisoformat(last[0]) < throttle
                and (last[1] == w.utilization and last[2] == resets)
            ):
                continue
        rows.append(
            (
                ts.isoformat(),
                w.key,
                w.label,
                w.scope,
                float(w.utilization),
                w.resets_at.isoformat() if w.resets_at else None,
                w.source or quota.source,
            )
        )
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?,?,?,?)", rows)
    return conn.total_changes - before


def import_desktop(conn, path: Path | None = None) -> tuple[int, str]:
    """Copy new desktop samples into the series. (org, t) is the identity."""
    rows, note = desktop_samples(path)
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO quota_samples VALUES (?,?,?,?,?,?,?)", rows)
    return conn.total_changes - before, note


def canonical_scope(conn):
    """Map account-wide samples onto the desktop org when there is exactly one.

    The statusline and the endpoint do not name the org; the desktop history
    does. With one org they are the same series and must not show twice.
    """
    orgs = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT scope FROM quota_samples WHERE scope LIKE 'org:%'"
        )
    ]
    target = orgs[0] if len(orgs) == 1 else None
    return lambda scope: target if target and scope == "account" else scope


def latest(
    conn, *, now: datetime | None = None, max_age: timedelta | None = None
) -> Quota | None:
    """The newest sample of every window, from any source."""
    now = now or datetime.now(UTC)
    canon = canonical_scope(conn)
    newest_rows: dict[tuple[str, str], object] = {}
    for r in conn.execute(
        "SELECT q.* FROM quota_samples q JOIN ("
        "  SELECT key, scope, MAX(ts) ts FROM quota_samples GROUP BY key, scope"
        ") n ON q.key = n.key AND q.scope = n.scope AND q.ts = n.ts"
    ):
        series = (r["key"], canon(r["scope"]))
        if series not in newest_rows or r["ts"] > newest_rows[series]["ts"]:
            newest_rows[series] = r
    windows = []
    newest = None
    for r in newest_rows.values():
        stamp = datetime.fromisoformat(r["ts"])
        resets = _parse_time(r["resets_at"])
        if max_age is not None and now - stamp > max_age:
            continue
        if resets is not None and resets <= now:
            continue  # that window has rolled over since
        length = window_length(r["key"])
        if resets is None and length is not None and now - stamp > length:
            continue
        windows.append(
            Window(
                r["key"], r["utilization"], resets, r["scope"], r["source"], r["label"]
            )
        )
        newest = max(newest or stamp, stamp)
    if not windows:
        return None
    sources = {w.source for w in windows}
    order = {k: i for i, k in enumerate(_LABELS)}
    windows.sort(key=lambda w: (order.get(w.key, 99), w.key, w.scope))
    return Quota(windows, sources.pop() if len(sources) == 1 else "several", newest)


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def maybe_fetch(conn, config: dict, now: datetime | None = None) -> str:
    """Call the endpoint only when every rule allows it. Returns a note."""
    now = now or datetime.now(UTC)
    if config.get("offline") or not config.get("quota", True):
        return "offline"
    if latest(conn, now=now, max_age=FRESH):
        return ""
    after = _parse_time(_meta(conn, "quota_fetch_after"))
    if after and now < after:
        return f"endpoint paused until {after.isoformat()}"
    if not read_token():
        return "not signed in (no Claude Code OAuth token found)"
    strikes = int(_meta(conn, "quota_429s") or 0)
    try:
        quota = fetch()
    except RateLimited:
        wait = min(BACKOFF[1], BACKOFF[0] * (2**strikes))
        _set_meta(conn, "quota_429s", str(strikes + 1))
        _set_meta(conn, "quota_fetch_after", (now + wait).isoformat())
        return "endpoint rate limited; backing off"
    _set_meta(conn, "quota_429s", "0")
    _set_meta(conn, "quota_fetch_after", (now + FETCH_EVERY).isoformat())
    if quota is None:
        return "endpoint unreachable or returned nothing"
    record(conn, quota)
    return ""


def refresh(
    conn, config: dict, *, allow_fetch: bool, now: datetime | None = None
) -> list[str]:
    """Pull every local source into the series, then the endpoint if allowed."""
    from . import identity

    if identity.selected_account() != identity.account_id():
        return ["selected account differs from local credentials; stored quota only"]
    notes = []
    if identity.selected_account() == "unknown":
        _, note = import_desktop(conn)
        if note:
            notes.append(note)
        cached = config.get("statusline_cache_path")
        if cached:
            record(conn, from_statusline(cached, now), throttle=STATUSLINE_THROTTLE)
    else:
        notes.append(
            "unattributed desktop/statusline caches excluded from identified account"
        )
    if allow_fetch:
        note = maybe_fetch(conn, config, now)
        if note and note != "offline":
            notes.append(note)
    return notes


def read(config: dict, *, allow_fetch: bool = True) -> tuple[Quota | None, str]:
    """Best current reading, plus a human explanation when there is none."""
    from . import store

    if not config.get("quota", True):
        return None, "disabled in config"
    import sqlite3

    try:
        with store.connect() as conn:
            notes = refresh(conn, config, allow_fetch=allow_fetch)
            quota = latest(conn)
    except (sqlite3.Error, OSError) as exc:
        # A broken store must not cost the reading; there is just no series.
        from . import identity

        if (
            allow_fetch
            and not config.get("offline")
            and identity.selected_account() == identity.account_id()
        ):
            try:
                live = fetch()
            except RateLimited:
                live = None
            if live is not None:
                return live, ""
        return None, f"store unavailable: {exc}"
    if quota is None:
        return None, "; ".join(
            notes
        ) or "no reading yet: run `vibewatt statusline` from Claude Code"
    quota.notes = notes
    return quota, ""
