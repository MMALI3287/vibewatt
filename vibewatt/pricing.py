"""Model rates in USD per million tokens.

Resolution order for a model, first hit wins:

  1. ``pricing_overrides`` from the user's config
  2. the built-in table below
  3. a refreshed community table (LiteLLM), cached on disk

The built-in table outranks the remote one on purpose. Community tables lag
behind new model releases, and a lagging table is worse than no table: it
silently prices a current model at zero instead of saying it does not know.
Remote data is therefore only ever used to fill gaps, never to override a rate
that was transcribed from Anthropic's published pricing.

Built-in rates: https://platform.claude.com/docs/en/about-claude/pricing
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import NamedTuple

MILLION = 1_000_000
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
CACHE_TTL_SECONDS = 24 * 3600


class Rate(NamedTuple):
    input: float
    cache_5m: float
    cache_1h: float
    cache_read: float
    output: float


# Verified 2026-09-15 against the published pricing page.
BUILTIN: dict[str, Rate] = {
    # Retrieved 2026-09-26: https://www.anthropic.com/news/claude-3-7-sonnet
    # Cache multipliers: https://platform.claude.com/docs/en/about-claude/pricing
    "claude-3-5-sonnet": Rate(3, 3.75, 6, 0.30, 15),
    "claude-3-7-sonnet": Rate(3, 3.75, 6, 0.30, 15),
    # Retrieved 2026-09-26: https://www.anthropic.com/project/glasswing
    "claude-mythos-preview": Rate(25, 31.25, 50, 2.50, 125),
    "claude-fable-5-1": Rate(10.0, 12.50, 20.0, 0.25, 50.0),
    "claude-mythos-5-1": Rate(10.0, 12.50, 20.0, 0.25, 50.0),
    "claude-fable-5": Rate(10.0, 12.50, 20.0, 1.00, 50.0),
    "claude-mythos-5": Rate(10.0, 12.50, 20.0, 1.00, 50.0),
    "claude-opus-5": Rate(5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-8": Rate(5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-7": Rate(5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-6": Rate(5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-5": Rate(5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-1": Rate(15.0, 18.75, 30.0, 1.50, 75.0),
    "claude-opus-4": Rate(15.0, 18.75, 30.0, 1.50, 75.0),
    "claude-sonnet-5": Rate(2.0, 2.50, 4.0, 0.20, 10.0),
    "claude-sonnet-4-6": Rate(3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4-5": Rate(3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4": Rate(3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-haiku-4-5": Rate(1.0, 1.25, 2.0, 0.10, 5.0),
    "claude-haiku-3-5": Rate(0.80, 1.00, 1.60, 0.08, 4.0),
    "claude-3-5-haiku": Rate(0.80, 1.00, 1.60, 0.08, 4.0),
    "claude-3-opus": Rate(15.0, 18.75, 30.0, 1.50, 75.0),
    "claude-3-haiku": Rate(0.25, 0.30, 0.50, 0.03, 1.25),
}

# Fast mode replaces base input/output pricing; cache multipliers ride on top.
FAST_MODE: dict[str, Rate] = {
    "claude-opus-5": Rate(10.0, 12.50, 20.0, 1.00, 50.0),
    "claude-opus-4-8": Rate(10.0, 12.50, 20.0, 1.00, 50.0),
}

# Historical availability and rates retrieved 2026-09-26:
# https://platform.claude.com/docs/en/release-notes/overview
# https://platform.claude.com/docs/en/about-claude/pricing?38d7aa68_page=5&fcdaa149_page=1&fcdaa149_sort_date=desc&query=deliverability
# Earlier launch/promotion rates are intentionally unpriced until verified.
FAST_PERIODS = {
    "claude-opus-4-6": (("2026-05-12", "2026-06-29", Rate(30, 37.5, 60, 3, 150)),),
    "claude-opus-4-7": (("2026-05-12", "2026-07-24", Rate(30, 37.5, 60, 3, 150)),),
}

# Launch dates: same release-notes source, retrieved 2026-09-26.
FAST_CURRENT_START = {"claude-opus-4-8": "2026-05-28", "claude-opus-5": "2026-07-24"}

GEO_US_MULTIPLIER = 1.1
WEB_SEARCH_PER_CALL = 10.0 / 1000

_remote: dict[str, Rate] | None = None


def fingerprint(overrides: dict | None = None) -> str:
    """Identify the current pricing inputs without triggering a network fetch."""
    snapshot = {
        "algorithm": 2,
        "builtin": BUILTIN,
        "fast": FAST_MODE,
        "fast_periods": FAST_PERIODS,
        "fast_current_start": FAST_CURRENT_START,
        "long_context": LONG_CONTEXT,
        "remote": _remote or {},
        "overrides": overrides or {},
        "geo_us": GEO_US_MULTIPLIER,
        "web_search": WEB_SEARCH_PER_CALL,
    }
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# Bedrock inference profiles (global., us., eu., apac., jp., au., us-gov., ...)
# and the bare "anthropic." provider prefix (A-012).
_PROVIDER = re.compile(r"^(?:[a-z]{2,6}(?:-[a-z]+)?\.)?anthropic[./]")
_SUFFIXES = (
    re.compile(r"\[1m\]$"),  # Claude Code's 1M-context marker
    re.compile(r"@.*$"),  # Vertex snapshot: claude-opus-4-5@20251101
    re.compile(r"-v\d+(?::\d+)?$"),  # Bedrock version: -v1:0
    re.compile(r"-\d{8}$"),  # dated snapshot: -20250929
    re.compile(r"-latest$"),
)


def normalize(model: str | None) -> str | None:
    """The family id a rate is keyed on: provider prefix and snapshot suffixes gone."""
    if not model:
        return None
    m = _PROVIDER.sub("", model.strip().lower())
    for suffix in _SUFFIXES:
        m = suffix.sub("", m)
    return m or None


def _cache_path():
    from .config import data_dir

    return data_dir() / "pricing-cache.json"


def _load_remote_cache(*, allow_stale: bool = False) -> dict | None:
    path = _cache_path()
    try:
        if not allow_stale and time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# The table is ~2 MB today. A bound before it is enabled, not after (A-102).
REMOTE_MAX_BYTES = 16 * 1024 * 1024


def _fetch_remote(timeout: float = 10.0) -> dict | None:
    try:
        with urllib.request.urlopen(LITELLM_URL, timeout=timeout) as resp:
            raw = resp.read(REMOTE_MAX_BYTES + 1)
        if len(raw) > REMOTE_MAX_BYTES:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass
    return payload


def _per_million(value, *, positive: bool) -> float:
    rate = float(value) * MILLION
    if not math.isfinite(rate) or rate < 0 or (positive and rate == 0):
        raise ValueError(f"not a usable rate: {value!r}")
    return rate


def _parse_remote(payload: dict) -> dict[str, Rate]:
    """Convert per-token costs into our per-million Rate shape.

    An entry with a zero, negative, NaN or missing input/output cost is
    dropped, never used: a zero rate is exactly the silent undercount the
    unpriced flag exists to prevent (A-066).
    """
    out: dict[str, Rate] = {}
    for name, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("litellm_provider") != "anthropic":
            continue
        try:
            inp = _per_million(entry["input_cost_per_token"], positive=True)
            outp = _per_million(entry["output_cost_per_token"], positive=True)
            write = entry.get("cache_creation_input_token_cost")
            read = entry.get("cache_read_input_token_cost")
            w5 = (
                _per_million(write, positive=False) if write is not None else inp * 1.25
            )
            rd = _per_million(read, positive=False) if read is not None else inp * 0.1
        except (KeyError, TypeError, ValueError):
            continue
        key = normalize(name)
        if key:
            # Community tables carry a single cache-write figure, which is the
            # 5m rate. The 1h rate is a fixed 2x of base input.
            out[key] = Rate(inp, w5, inp * 2.0, rd, outp)
    return out


def refresh(offline: bool = False) -> int:
    """Populate the remote gap-filling table. Returns how many models it holds."""
    global _remote
    payload = _load_remote_cache()
    if payload is None and not offline:
        payload = _fetch_remote()
    if payload is None:
        # An empty table would change the fingerprint and reprice every
        # remote-only model to unpriced while offline. Stale rates beat none.
        payload = _load_remote_cache(allow_stale=True)
    _remote = _parse_remote(payload) if payload else {}
    return len(_remote)


def rate_for(
    model: str | None,
    *,
    fast: bool = False,
    ts: datetime | None = None,
    prompt_tokens: int = 0,
    geo: str | None = None,
    overrides: dict | None = None,
) -> Rate | None:
    """Return the billing rate for a model, or None when nothing knows it.

    Lookup is by exact family id. An id no table knows is unpriced, never
    priced at an older sibling's rate: Opus 4.5 launched at a third of Opus 4's
    price, so a prefix match would have overpriced it 3x (A-011). Fast mode on
    a model with no published fast rate is unpriced too.
    """
    m = normalize(model)
    if not m:
        return None

    rate = None
    if overrides:
        entry = overrides.get(m) or overrides.get(model or "")
        if isinstance(entry, dict):
            base = entry.get("input")
            if base is not None:
                rate = Rate(
                    float(base),
                    float(entry.get("cache_5m", float(base) * 1.25)),
                    float(entry.get("cache_1h", float(base) * 2.0)),
                    float(entry.get("cache_read", float(base) * 0.1)),
                    float(entry.get("output", float(base) * 5.0)),
                )
    if rate is None and fast:
        if m in FAST_PERIODS:
            day = ts.astimezone(UTC).date().isoformat() if ts else ""
            rate = next(
                (r for start, end, r in FAST_PERIODS[m] if start <= day < end), None
            )
        else:
            if ts is not None and ts.astimezone(
                UTC
            ).date().isoformat() < FAST_CURRENT_START.get(m, "9999"):
                return None
            rate = FAST_MODE.get(m)
        if rate is None:
            return None
    if rate is None:
        rate = BUILTIN.get(m)
    if rate is None and _remote:
        rate = _remote.get(m)
    if rate is None:
        return None
    if not fast and prompt_tokens > 200_000 and ts is not None:
        day = ts.astimezone(UTC).date().isoformat()
        period = LONG_CONTEXT.get(m)
        if period and period[0] <= day < period[1]:
            rate = Rate(
                rate.input * 2,
                rate.cache_5m * 2,
                rate.cache_1h * 2,
                rate.cache_read * 2,
                rate.output * 1.5,
            )
    if geo == "us":
        rate = Rate(*(v * GEO_US_MULTIPLIER for v in rate))
    return rate


# Retrieved 2026-09-26. Launch premium and GA removal:
# https://www.anthropic.com/news/claude-opus-4-6
# https://platform.claude.com/docs/en/release-notes/overview#march-13-2026
# Sonnet 4 threshold/rates: https://claude.com/blog/1m-context (2025-08-12).
# Beta end: release notes 2026-04-30. Retrieved 2026-09-26.
LONG_CONTEXT = {
    "claude-opus-4-6": ("2026-02-05", "2026-03-13"),
    "claude-sonnet-4": ("2025-08-12", "2026-04-30"),
}


def context_premium_unknown(turn) -> bool:
    """Flag large prompts whose date/model premium is not verified."""
    if turn.input + turn.cache_read + turn.cache_5m + turn.cache_1h <= 200_000:
        return False
    model = normalize(turn.model)
    day = turn.ts.astimezone(UTC).date().isoformat()
    if model in LONG_CONTEXT and LONG_CONTEXT[model][0] <= day < LONG_CONTEXT[model][1]:
        return False
    # Current first-party docs explicitly exempt these model families.
    exempt = {
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-mythos-5",
        "claude-mythos-5-1",
        "claude-mythos-preview",
    }
    return not (model in exempt and day >= "2026-03-13")
