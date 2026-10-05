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
from datetime import UTC, datetime, timedelta
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
    # Retrieved 2026-10-05: https://platform.claude.com/docs/en/about-claude/pricing
    # Opus 5.5 cache reads are 0.05x base input, not 0.1x.
    "claude-opus-5-5": Rate(4.0, 5.0, 8.0, 0.20, 20.0),
    "claude-sonnet-5-5": Rate(2.0, 2.50, 4.0, 0.20, 10.0),
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
    # Retrieved 2026-10-05, pricing page above: $8/$40, cache read 0.05x input.
    "claude-opus-5-5": Rate(8.0, 10.0, 16.0, 0.40, 40.0),
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

# Launch dates: same release-notes source, retrieved 2026-09-26. Opus 5.5 is
# listed under 2026-09-24 (retrieved 2026-10-05). Local logs show it a day
# earlier; fast turns before the listed date stay unpriced rather than guessed.
FAST_CURRENT_START = {
    "claude-opus-4-8": "2026-05-28",
    "claude-opus-5": "2026-07-24",
    "claude-opus-5-5": "2026-09-24",
}

# Verified dated built-in rates (discounts, price changes): model ->
# ((start, end, Rate), ...), UTC days, end exclusive. Checked before BUILTIN so
# a response is priced at the rate in effect when it happened.
BUILTIN_PERIODS: dict[str, tuple[tuple[str, str, Rate], ...]] = {
    # OpenAI models seen in Codex logs. Rates: developers.openai.com/api/docs/
    # models/<id>, retrieved 2026-10-05. Start days are the API release dates in
    # https://developers.openai.com/api/docs/changelog (same retrieval); no price
    # change after release was listed. OpenAI bills no separate cache-write rate
    # for gpt-5.5, so its cache writes use the input rate. These ids are
    # dated, not in BUILTIN: usage before the release stays unpriced.
    # Rate fields: input, cache write, cache write (1h, unused), cache read, output.
    "gpt-5.5": (("2026-04-24", "9999-12-31", Rate(5.0, 5.0, 5.0, 0.50, 30.0)),),
    "gpt-6-astra": (("2026-09-03", "9999-12-31", Rate(10.0, 12.5, 12.5, 1.0, 50.0)),),
    "gpt-6.1-sol": (("2026-09-29", "9999-12-31", Rate(2.0, 2.5, 2.5, 0.10, 10.0)),),
    "gpt-5.4": (("2026-03-05", "9999-12-31", Rate(2.5, 2.5, 2.5, 0.25, 15.0)),),
    # The changelog's 2026-07-30 entry cut Terra by 20%; the model page lists only
    # the new price, with cache writes at 1.25x input. Earlier usage stays unpriced.
    "gpt-5.6-terra": (("2026-07-30", "9999-12-31", Rate(2.0, 2.5, 2.5, 0.20, 12.0)),),
    # Codex stamps approval-review turns "codex-auto-review", a slug with no API
    # page. OpenAI's auto-review report (alignment.openai.com/auto-review,
    # published 2026-04-30) names the reviewer GPT-5.4 Thinking at low reasoning,
    # so gpt-5.4 rates apply from that date.
    "codex-auto-review": (
        ("2026-04-30", "9999-12-31", Rate(2.5, 2.5, 2.5, 0.25, 15.0)),
    ),
    # Promotional price from the 2026-08-21 changelog entry, "available at least
    # through November 21, 2026". The window closes at 2026-11-22 so later usage
    # stays unpriced until the next rate is read. Before 2026-08-21 it was
    # $5 / $30 per the same entry's "20% lower input, 33% lower output", but the
    # cache rates for that period are not listed, so that period is not priced.
    "gpt-5.6-sol": (("2026-08-21", "2026-11-22", Rate(4.0, 5.0, 5.0, 0.40, 20.0)),),
}

# Prompts past this size reprice the whole request: 2x input and cache rates,
# 1.5x output (the model pages above). Only models whose page states the
# threshold are listed; any other OpenAI model over it is flagged, not guessed.
OPENAI_LONG_CONTEXT_TOKENS = 272_000
OPENAI_LONG_CONTEXT = frozenset(
    {
        "gpt-5.4",
        "gpt-5.5",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "gpt-6.1-sol",
        "codex-auto-review",
    }
)

GEO_US_MULTIPLIER = 1.1
WEB_SEARCH_PER_CALL = 10.0 / 1000

_remote: dict[str, Rate] | None = None
# Every remote rate change observed, so a later discount never reprices older
# usage: model -> [(first UTC day seen, Rate), ...], oldest first.
_history: dict[str, list[tuple[str, Rate]]] = {}
_windows: dict[str, int] = {}
# Rates change a few times a year; the cap only stops a corrupt or hostile
# table from growing the file without limit. The oldest entry is never dropped.
HISTORY_PER_MODEL = 64


def fingerprint(overrides: dict | None = None) -> str:
    """Identify the current pricing inputs without triggering a network fetch."""
    snapshot = {
        "algorithm": 2,
        "builtin": BUILTIN,
        "fast": FAST_MODE,
        "fast_periods": FAST_PERIODS,
        "fast_current_start": FAST_CURRENT_START,
        "long_context": LONG_CONTEXT,
        "openai_long_context": [
            OPENAI_LONG_CONTEXT_TOKENS,
            sorted(OPENAI_LONG_CONTEXT),
        ],
        "remote": _remote or {},
        "history": _history,
        "builtin_periods": BUILTIN_PERIODS,
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


def provider_specific(model: str) -> bool:
    """True for Bedrock and Vertex spellings, whose limits can differ from the API."""
    m = model.strip().lower()
    return bool(_PROVIDER.match(m) or "@" in m or _SUFFIXES[2].search(m))


def _history_path():
    from .config import data_dir

    return data_dir() / "pricing-history.json"


def _load_history() -> dict[str, list[tuple[str, Rate]]]:
    try:
        raw = json.loads(_history_path().read_text(encoding="utf-8"))
        return {
            str(model): [(str(day), Rate(*map(float, rate))) for day, rate in entries]
            for model, entries in raw.items()
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _record_history(rates: dict[str, Rate], day: str) -> None:
    global _history
    if not _history:
        _history = _load_history()
    changed = False
    for model, rate in rates.items():
        entries = _history.setdefault(model, [])
        if entries and entries[-1][1] == rate:
            continue
        if entries and day < entries[-1][0]:
            continue  # an older cache cannot rewrite what came after it
        if entries and day == entries[-1][0]:
            entries[-1] = (day, rate)
        else:
            entries.append((day, rate))
        if len(entries) > HISTORY_PER_MODEL:
            del entries[1]
        changed = True
    if changed:
        path = _history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_history), encoding="utf-8")
        except OSError:
            pass


def _remote_at(model: str, ts: datetime | None) -> Rate | None:
    entries = _history.get(model)
    if not entries:
        return _remote.get(model) if _remote else None
    if ts is None:
        return entries[-1][1]
    day = ts.astimezone(UTC).date().isoformat()
    # Usage older than the first observation gets the earliest known rate.
    rate = entries[0][1]
    for start, candidate in entries:
        if start <= day:
            rate = candidate
    return rate


def remote_window(model: str | None) -> int | None:
    """The context window the community table lists, for models we do not list."""
    m = normalize(model)
    return _windows.get(m) if m else None


def cache_age_days(now: datetime | None = None) -> float | None:
    try:
        mtime = _cache_path().stat().st_mtime
    except OSError:
        return None
    return ((now or datetime.now(UTC)).timestamp() - mtime) / 86400


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


def _parse_windows(payload: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, entry in payload.items():
        if not isinstance(entry, dict) or entry.get("litellm_provider") != "anthropic":
            continue
        window = entry.get("max_input_tokens")
        key = normalize(name)
        if key and type(window) is int and 0 < window <= 100_000_000:
            out[key] = window
    return out


def refresh(offline: bool = False) -> int:
    """Populate the remote gap-filling table. Returns how many models it holds."""
    global _remote, _windows
    payload = _load_remote_cache()
    if payload is None and not offline:
        payload = _fetch_remote()
    if payload is None:
        # An empty table would change the fingerprint and reprice every
        # remote-only model to unpriced while offline. Stale rates beat none.
        payload = _load_remote_cache(allow_stale=True)
    _remote = _parse_remote(payload) if payload else {}
    _windows = _parse_windows(payload) if payload else {}
    if _remote:
        age = cache_age_days()
        seen = datetime.now(UTC) - timedelta(days=age or 0)
        _record_history(_remote, seen.date().isoformat())
    elif not _history:
        _history.update(_load_history())
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
    if rate is None and m in BUILTIN_PERIODS and ts is not None:
        day = ts.astimezone(UTC).date().isoformat()
        rate = next(
            (r for start, end, r in BUILTIN_PERIODS[m] if start <= day < end), None
        )
    if rate is None:
        rate = BUILTIN.get(m)
    if rate is None:
        rate = _remote_at(m, ts)
    if rate is None:
        return None
    if (
        m in OPENAI_LONG_CONTEXT
        and not fast
        and prompt_tokens > OPENAI_LONG_CONTEXT_TOKENS
    ):
        rate = Rate(
            rate.input * 2,
            rate.cache_5m * 2,
            rate.cache_1h * 2,
            rate.cache_read * 2,
            rate.output * 1.5,
        )
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
    prompt = turn.input + turn.cache_read + turn.cache_5m + turn.cache_1h
    model = normalize(turn.model)
    if model and model.startswith(("gpt-", "codex-")):
        # A known threshold is applied by rate_for; an unlisted one is unverified.
        return model not in OPENAI_LONG_CONTEXT and prompt > OPENAI_LONG_CONTEXT_TOKENS
    if prompt <= 200_000:
        return False
    day = turn.ts.astimezone(UTC).date().isoformat()
    if model in LONG_CONTEXT and LONG_CONTEXT[model][0] <= day < LONG_CONTEXT[model][1]:
        return False
    # Current first-party docs explicitly exempt these model families.
    exempt = {
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-mythos-5",
        "claude-mythos-5-1",
        "claude-mythos-preview",
    }
    return not (model in exempt and day >= "2026-03-13")
