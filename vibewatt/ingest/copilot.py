"""GitHub Copilot Chat in VS Code: User/{workspaceStorage/*/chatSessions,
globalStorage/emptyWindowChatSessions}/*.jsonl (or .json).

A `.jsonl` session is a change journal, not a list of records: a `kind 0`
snapshot of the session, then `kind 1` sets of one value at a key path and
`kind 2` appends to the array at a key path. Replaying it rebuilds the session,
whose `requests` are the billable unit. See docs/DATA-SOURCES.md, "Copilot".
"""

from __future__ import annotations

import json
import os
import platform
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from vibewatt.sources import COPILOT, Turn, _int, _parse_ts

_PRODUCTS = ("Code", "Code - Insiders")
# A key segment that could reach an object's machinery instead of its data.
_FORBIDDEN = {"__proto__", "prototype", "constructor"}
# The router id. The model that answered is in result.metadata.resolvedModel.
_ROUTER = "copilot/auto"


def user_dirs() -> list[Path]:
    """VS Code `User` folders. VIBEWATT_VSCODE_USER_DIRS overrides the defaults."""
    configured = os.environ.get("VIBEWATT_VSCODE_USER_DIRS")
    if configured:
        return [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [base / product / "User" for product in _PRODUCTS]


def discover(cfg: dict | None = None) -> list[Path]:
    from . import walk

    files: list[Path] = []
    for user in user_dirs():
        workspaces = user / "workspaceStorage"
        if workspaces.is_dir():
            for chats in sorted(workspaces.glob("*/chatSessions")):
                files.extend(_sessions(chats, walk))
        empty = user / "globalStorage" / "emptyWindowChatSessions"
        if empty.is_dir():
            files.extend(_sessions(empty, walk))
    return files


def _sessions(folder: Path, walk) -> list[Path]:
    # When both forms of one session exist the journal is newer and wins.
    journals = walk(folder, "*.jsonl")
    stems = {p.stem for p in journals}
    snapshots = [p for p in walk(folder, "*.json") if p.stem not in stems]
    return journals + snapshots


def _container(state, keys: list):
    cur = state
    for key in keys:
        if isinstance(key, str) and key in _FORBIDDEN:
            raise KeyError(key)
        cur = cur[key]
    return cur


def apply(state, entry: dict):
    """Apply one journal entry to the session state and return the new state."""
    kind = entry.get("kind")
    if kind == 0:
        return entry.get("v")
    keys = entry.get("k")
    if not isinstance(keys, list) or not keys or not isinstance(state, dict):
        raise ValueError("journal entry without a key path")
    parent = _container(state, keys[:-1])
    last = keys[-1]
    if isinstance(last, str) and last in _FORBIDDEN:
        raise KeyError(last)
    if kind == 1:
        if isinstance(parent, list):
            parent[last] = entry.get("v")  # IndexError when the slot is absent
        else:
            parent[last] = entry.get("v")
    elif kind == 2:
        target = parent[last]
        if not isinstance(target, list):
            raise TypeError("append to a non-array")
        value = entry.get("v")
        target.extend(value if isinstance(value, list) else [value])
    else:
        raise ValueError(f"unknown journal kind {kind!r}")
    return state


def _load(path: Path, drops: Counter) -> dict | None:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        if path.suffix == ".json":
            try:
                state = json.load(handle)
            except json.JSONDecodeError:
                drops["bad_json"] += 1
                return None
            return state if isinstance(state, dict) else None
        state = None
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                drops["bad_json"] += 1
                continue
            if not isinstance(entry, dict):
                drops["not_an_object"] += 1
                continue
            try:
                state = apply(state, entry)
            except (KeyError, IndexError, TypeError, ValueError):
                # One entry that does not fit the replayed state is skipped;
                # the rest of the journal still applies.
                drops["bad_journal_entry"] += 1
        return state if isinstance(state, dict) else None


def _project(path: Path) -> str:
    # workspaceStorage/<hash>/chatSessions/<id>.jsonl names its workspace in
    # workspaceStorage/<hash>/workspace.json as a file URI.
    try:
        raw = json.loads((path.parent.parent / "workspace.json").read_text("utf-8"))
        uri = raw.get("folder") or raw.get("workspace") or ""
        name = Path(unquote(urlparse(uri).path).rstrip("/")).name
        return name.removesuffix(".code-workspace") or "copilot"
    except (OSError, ValueError, AttributeError, TypeError):
        return "copilot"


def _count(value) -> int | None:
    if value is None:
        return None
    return _int(value)


def parse(
    path: Path, drops: Counter | None = None, raw: dict | None = None
) -> Iterator[Turn]:
    """One Turn per completed request, not deduped.

    Output is `completionTokens`, the request total across every tool-call
    round. Input is the prompt VS Code recorded, which is the last round's
    prompt only: Copilot does not log the earlier rounds' input. `copilotCredits`
    (1 credit = $0.01) is the amount billed for the whole request and becomes
    the cost when present. Raises OSError on an unreadable file.
    """
    drops = drops if drops is not None else Counter()
    state = _load(path, drops)
    if state is None:
        return
    session = str(state.get("sessionId") or path.stem)
    project = _project(path)
    requests = state.get("requests")
    if not isinstance(requests, list):
        return
    for request in requests:
        if not isinstance(request, dict):
            drops["not_an_object"] += 1
            continue
        result = request.get("result")
        metadata = result.get("metadata") if isinstance(result, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}
        try:
            prompt = _count(request.get("promptTokens"))
            if prompt is None:
                prompt = _count(metadata.get("promptTokens"))
            output = _count(request.get("completionTokens"))
            if output is None:
                output = _count(metadata.get("outputTokens"))
            credits = request.get("copilotCredits")
            if credits is not None and (
                isinstance(credits, bool)
                or not isinstance(credits, (int, float))
                or credits < 0
            ):
                raise TypeError("copilotCredits is not a number")
        except (TypeError, ValueError, OverflowError):
            drops["bad_field"] += 1
            continue
        if not prompt and not output:
            drops["no_usage"] += 1
            continue
        stamp = request.get("timestamp")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            ts = _parse_ts(stamp) if isinstance(stamp, str) else None
        else:
            try:
                ts = datetime.fromtimestamp(stamp / 1000, UTC)
            except (OverflowError, OSError, ValueError):
                ts = None
        if ts is None:
            drops["bad_timestamp"] += 1
            continue
        model = metadata.get("resolvedModel") or request.get("modelId")
        if not isinstance(model, str) or not model or model == _ROUTER:
            drops["no_model"] += 1
            continue
        request_id = request.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            drops["no_request_id"] += 1
            continue
        response_id = request.get("responseId")
        yield Turn(
            source=COPILOT,
            ts=ts,
            model=model.removeprefix("copilot/"),
            input=prompt or 0,
            cache_5m=0,
            cache_1h=0,
            cache_read=0,
            output=output or 0,
            thinking=0,
            web_searches=0,
            fast=False,
            geo=None,
            sidechain=False,
            project=project,
            session=session,
            key=(
                f"copilot:{request_id}",
                response_id if isinstance(response_id, str) else "",
            ),
            billed_usd=None if credits is None else float(credits) / 100,
        )


# --- plan readings ------------------------------------------------------------


def cache_path() -> Path:
    configured = os.environ.get("VIBEWATT_COPILOT_CACHE")
    if configured:
        return Path(configured).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".cache"
    return base / "copilot" / "copilot-user-cache.json"


def plan_readings() -> list[tuple]:
    """Premium-request use per GitHub login, as quota_samples rows.

    Copilot caches each signed-in account's entitlement response locally. Only
    the login, plan, quota snapshot and reset date are read. A file that is
    absent or unreadable gives no readings: plan meters degrade, sync goes on.
    """
    try:
        text = cache_path().read_text(encoding="utf-8-sig")
        body = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("//")
        )
        accounts = json.loads(body).get("copilotUserCache")
    except (OSError, ValueError, AttributeError):
        return []
    rows = []
    for entry in (accounts or {}).values() if isinstance(accounts, dict) else []:
        if not isinstance(entry, dict):
            continue
        response = entry.get("response")
        stamp = _parse_ts(entry.get("retrievedAt"))
        if not isinstance(response, dict) or stamp is None:
            continue
        login = response.get("login")
        snapshot = (response.get("quota_snapshots") or {}).get("premium_interactions")
        if not isinstance(login, str) or not login or not isinstance(snapshot, dict):
            continue
        left = snapshot.get("percent_remaining")
        if snapshot.get("unlimited") or isinstance(left, bool):
            continue
        if not isinstance(left, (int, float)):
            continue
        plan = response.get("copilot_plan")
        plan = plan if isinstance(plan, str) and plan else "unknown plan"
        resets = _parse_ts(response.get("quota_reset_date_utc"))
        rows.append(
            (
                stamp.isoformat(),
                "copilot_premium",
                f"Copilot premium requests ({plan})",
                f"github:{login}",
                round(100.0 - float(left), 6),
                resets.isoformat() if resets else None,
                "copilot-cache",
            )
        )
    return rows
