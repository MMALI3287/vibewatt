"""Command line entry point."""

from __future__ import annotations

import argparse
import csv
import errno
import io
import json
import re
import socket
import sys
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

from . import __version__, pricing, quota, terminal
from . import config as configmod
from ._cli_output import PipeOutput, StdoutClosed
from .aggregate import cost_of, from_store
from .ingest import discover
from .sources import ANTIGRAVITY, CLAUDE_CODE, CODEX, COPILOT, COWORK


def resolve_tz(name: str | None) -> tzinfo:
    if not name or name == "local":
        from tzlocal import get_localzone

        try:
            return get_localzone()
        except (LookupError, ValueError) as exc:
            # POSIX TZ strings (TZ=JST-9) and conflicting system configs have no
            # IANA name. A fixed offset beats crashing every command, statusline included.
            print(
                f"vibewatt: cannot resolve the local timezone ({exc}); using the "
                "current UTC offset. Set `timezone` in the config to an IANA name.",
                file=sys.stderr,
            )
            return datetime.now().astimezone().tzinfo or UTC
    if name == "utc":
        return UTC
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"unknown timezone {name!r}: {exc}")


# Other providers' plans, each shown apart from the Anthropic plan.
PROVIDER_PLANS = {
    CODEX: "Codex plan, ChatGPT account, from local logs",
    COPILOT: "Copilot plan, GitHub account, from Copilot's local cache",
}


def provider_quota(provider: str):
    """The newest plan readings for one provider in the store. Never a network call."""
    from . import quota, store

    with store.connect() as conn:
        return quota.latest(conn, provider=provider)


def report_zone(cfg: dict):
    """The report timezone, with the day boundary moved to `day_start_hour`."""
    try:
        return configmod.day_zone(
            resolve_tz(cfg.get("timezone")), cfg.get("day_start_hour")
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _parse_since_date(value: str, tz: tzinfo, *, now: datetime | None = None) -> date:
    """Resolve --since using the report's time zone, not the host's local date."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass

    today = (now if now is not None else datetime.now(tz)).astimezone(tz).date()
    if value == "this-month":
        return today.replace(day=1)
    if value == "last-month":
        return (today.replace(day=1) - timedelta(days=1)).replace(day=1)

    span = re.fullmatch(r"([1-9]\d*)([dw])", value)
    if span and len(span.group(1)) <= 6:
        days = int(span.group(1)) * (7 if span.group(2) == "w" else 1)
        try:
            return today - timedelta(days=days)
        except (OverflowError, ValueError):
            pass
    raise ValueError(
        f"--since expects YYYY-MM-DD, 7d, 2w, this-month or last-month, got {value!r}"
    )


def _bucket_dict(b) -> dict:
    return {
        "responses": b.turns,
        "input": b.input,
        "cache_write_5m": b.cache_5m,
        "cache_write_1h": b.cache_1h,
        "cache_read": b.cache_read,
        "output": b.output,
        "thinking": b.thinking,
        "web_searches": b.web_searches,
        "web_fetch": b.web_fetch,
        "code_execution": b.code_execution,
        "code_execution_cost": "unavailable" if b.code_execution else "not_used",
        "nonstandard_iterations": b.nonstandard_iterations,
        "context_premium_unknown": b.context_premium_unknown,
        "cost_usd": round(b.cost, 6),
        # Responses whose model has no known rate; cost_usd leaves them out (A-026).
        "unpriced": b.unpriced,
    }


def serialize(report) -> dict:
    active = report.active_block
    payload = {
        "total": _bucket_dict(report.total),
        "by_day": {str(d): _bucket_dict(b) for d, b in sorted(report.by_day.items())},
        "by_model": {m: _bucket_dict(b) for m, b in report.by_model.items()},
        "by_source": {s: _bucket_dict(b) for s, b in report.by_source.items()},
        "by_project": {p: _bucket_dict(b) for p, b in report.by_project.items()},
        "by_day_model": {
            f"{d}|{m}": _bucket_dict(b)
            for (d, m), b in sorted(report.by_day_model.items())
        },
        "by_hour": {str(h): _bucket_dict(b) for h, b in sorted(report.by_hour.items())},
        "sessions": len(report.sessions),
        "unknown_models": sorted(report.unknown_models),
        "restored_days": sorted(str(d) for d in report.restored_days),
        "cache_hit_rate": round(report.total.cache_hit_rate, 4),
    }
    if active is not None:
        tokens, cost = active.project_to_end()
        payload["active_block"] = {
            "start": active.start.isoformat(),
            "end": active.end.isoformat(),
            "tokens": active.bucket.total_tokens,
            "cost_usd": round(active.bucket.cost, 6),
            "tokens_per_minute": round(active.tokens_per_minute, 2),
            "projected_tokens": tokens,
            "projected_cost_usd": round(cost, 6),
        }
    return payload


def _cell(value):
    # A spreadsheet runs a cell that starts with = + - @ as a formula. Model and
    # project names come from logs, so they are neutralized (A-072).
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(report) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "date",
            "model",
            "responses",
            "input",
            "cache_write_5m",
            "cache_write_1h",
            "cache_read",
            "output",
            "cost_usd",
            "unpriced_responses",
        ]
    )
    for (day, model), b in sorted(report.by_day_model.items()):
        # An unpriced row has no cost at all, not a cost of zero.
        cost = "" if b.unpriced and not b.cost else f"{b.cost:.6f}"
        writer.writerow(
            [
                day,
                _cell(model),
                b.turns,
                b.input,
                b.cache_5m,
                b.cache_1h,
                b.cache_read,
                b.output,
                cost,
                b.unpriced,
            ]
        )
    return out.getvalue()


def statusline(cfg, tz, raw: str) -> str:
    """The `statusLine` command: record the plan windows Claude Code pipes in.

    Claude Code sends its statusline JSON on stdin at every refresh. This
    records a throttled quota sample, runs the user's own statusline command
    (`statusline_chain`) with the same input and prints one line. No network,
    no report building: it has to finish well inside Claude Code's refresh.
    """
    import sqlite3
    import subprocess

    from . import store

    try:
        blob = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        blob = None
    reading = quota.from_statusline_json(blob)
    parts = []
    if reading is not None:
        if cfg.get("quota", True):
            try:
                with store.connect() as conn:
                    quota.record(conn, reading, throttle=quota.STATUSLINE_THROTTLE)
            except (sqlite3.Error, OSError):
                pass  # a busy store must never blank the status line
        for w in reading.windows:
            text = f"{w.label} {w.utilization:.0f}%"
            if w.resets_at is not None:
                text += f" (resets {w.resets_at.astimezone(configmod.clock_zone(tz)):%H:%M})"
            parts.append(text)
    chain = cfg.get("statusline_chain")
    if chain:
        try:
            # The user's own command from their own config, run through a shell
            # exactly as Claude Code runs a statusLine command. Claude Code's
            # JSON goes in on stdin and is never interpolated into the command.
            done = subprocess.run(
                chain,
                shell=True,
                input=raw,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            first = done.stdout.strip().splitlines()[:1]
            parts = first + parts
        except (OSError, subprocess.SubprocessError):
            pass
    return "  |  ".join(parts)


def sync_store(cfg, tz, files=None, progress=None):
    """Bring the store up to date with the logs. Returns the SyncResult."""
    from . import identity, store
    from .sources import DEDUPE_VERSION

    if identity.selected_account() != identity.account_id():
        with store.connect() as conn:
            store.rebucket(conn, tz)
            store.reprice(conn, cfg.get("pricing_overrides"))
        return store.SyncResult()

    if pricing._remote is None:
        pricing.refresh(offline=True)
    files = discover(cfg) if files is None else files
    files = identity.unclaimed_files(files)
    overrides = cfg.get("pricing_overrides")
    with store.connect() as conn:
        dedupe_version = conn.execute(
            "SELECT value FROM meta WHERE key='dedupe_version'"
        ).fetchone()
        if dedupe_version is None or dedupe_version[0] != str(DEDUPE_VERSION):
            # Old checkpoints can hide geo evidence discarded by the previous merge.
            conn.execute("UPDATE files SET mtime=-1")
        result = store.sync_files(
            conn, files, tz, lambda t: cost_of(t, overrides), progress=progress
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('dedupe_version',?)",
            (str(DEDUPE_VERSION),),
        )
        store.reprice(conn, overrides)
        from . import activity
        from .ingest.claude_code import roots

        activity.sync(conn, [root.parent / "history.jsonl" for root in roots()])
        return result


def build_report(
    cfg,
    tz,
    *,
    source="all",
    date_from=None,
    date_to=None,
    project=None,
    model=None,
    refresh=False,
    with_quota=True,
    parts=None,
):
    """Report numbers, always from the SQLite store.

    Shared by the CLI and the API so filtering logic lives in exactly one
    place. `date_from`/`date_to` are inclusive `date` objects; `project` and
    `model` match a turn's exact project/model string. `refresh` syncs changed
    log files into the store first; request handlers leave it off because the
    server syncs in the background.
    """
    from . import store

    files: list = []
    duplicates = 0
    if refresh:
        files = discover(cfg)
        duplicates = sync_store(cfg, tz, files).duplicates
    from .aggregate import _merge_into
    from .projects import project_map, relabel_buckets, resolve

    with store.connect() as conn:
        labels = project_map(conn, cfg)
        report = from_store(
            conn,
            tz,
            source=source,
            date_from=date_from,
            date_to=date_to,
            project=resolve(labels, project, bool(cfg.get("mask_projects"))),
            model=model,
            include_sidechains=cfg.get("include_sidechains", True),
            session_hours=cfg.get("session_length_hours", 5),
            overrides=cfg.get("pricing_overrides"),
            parts=parts,
        )

    # Aliases and masking, the same mapping every endpoint uses.
    report.by_project = relabel_buckets(report.by_project, labels)
    cells: dict = {}
    for (day, src, raw, mdl), bucket in report.by_cell.items():
        key = (day, src, labels.get(raw, raw), mdl)
        if key in cells:
            _merge_into(cells[key], bucket)
        else:
            cells[key] = bucket
    report.by_cell = cells

    q, quota_note = quota.read(cfg) if with_quota else (None, None)
    return report, q, quota_note, duplicates, files


def harvest(args, cfg, tz) -> int:
    """Ingest a cloud session listing so web and Cowork remote usage is counted.

    Those sessions run in containers that are destroyed with the session, so no
    local file will ever hold them. The session API does report their totals,
    and this brings them in.
    """
    from . import store

    if not args.file:
        print(
            "vibewatt harvest --file sessions.json\n\n"
            "Cloud sessions (Claude Code on the web, Cowork remote) write no local\n"
            "logs, but the session API reports their usage. To produce the file, ask\n"
            "any Claude session:\n\n"
            "    List my Claude Code sessions with list_sessions (mine: true, limit 100,\n"
            "    paginating with after_id) and write the raw JSON to sessions.json\n\n"
            "Then run this command against it. Re-harvesting is idempotent.",
            file=sys.stderr,
        )
        return 2
    try:
        with open(args.file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"cannot read {args.file}: {exc}") from exc
    # Accept the tool's envelope or a bare list.
    if isinstance(payload, dict) and "ccr" in payload:
        payload = payload["ccr"]
    with store.connect() as conn:
        counts = store.upsert_cloud_sessions(conn, payload)
        info = store.summary(conn)
    if not any(counts.values()):
        # A file that holds no session listing is an error, not a silent success (A-031).
        raise SystemExit(
            f"{args.file} holds no session entries: expected a list, "
            '{"data": [...]} or {"ccr": ...}'
        )
    print(
        f"  harvested {counts['written']} session(s), skipped {counts['skipped']} with no "
        f"usage block, {counts['rejected_no_id']} without an id, "
        f"{counts['skipped_environment']} that ran locally"
    )
    for row in info["cloud_by_surface"]:
        print(
            f"    {row['surface']:<12} {row['n']:>4} session(s)   ${row['cost'] or 0:,.2f}"
        )
    return 0


def sync(args, cfg, tz) -> int:
    """Parse changed local logs into the store so later queries do not re-read them."""
    from . import store

    pricing.refresh(offline=cfg.get("offline", False))
    files = discover(cfg)

    def progress(done: int, total: int) -> None:
        if total > 200:
            print(f"  read {done}/{total} changed file(s)", file=sys.stderr)

    result = sync_store(cfg, tz, files, progress)
    print(
        f"  parsed {result.parsed} changed file(s), skipped {result.skipped} unchanged, "
        f"{len(files)} total"
    )
    print(
        f"  synced {result.turns} response(s), "
        f"{result.duplicates} content-block repeats collapsed"
    )
    print(f"  recovered {result.prompts} prompt title(s)")
    # One line per provider: a single total would blend Anthropic and OpenAI spend.
    with store.connect() as conn:
        held = conn.execute(
            "SELECT source, COUNT(*) n, MIN(day) lo, MAX(day) hi, SUM(cost) cost,"
            " SUM(cost IS NULL) unpriced FROM turns GROUP BY source ORDER BY source"
        ).fetchall()
    for row in held:
        unpriced = f", {row['unpriced']:,} unpriced" if row["unpriced"] else ""
        print(
            f"  store holds {row['n']:,} {row['source']} response(s)  "
            f"{row['lo']} .. {row['hi']}  ${row['cost'] or 0:,.2f}{unpriced}"
        )
    print(f"  database: {store.db_path()}")
    return 0


def sessions_report(args, cfg, tz) -> int:
    """Sessions across every surface, local and harvested, newest first."""
    from . import store

    with store.connect() as conn:
        rows = store.sessions(conn, limit=40)

    if not rows:
        print("  no sessions yet - run 'vibewatt sync' and 'vibewatt harvest' first")
        return 1
    print(
        f"\n  {'when':<11} {'surface':<12} {'what you worked on':<58} "
        f"{'tokens':>9} {'cost':>9}"
    )
    print("  " + "-" * 103)
    for r in rows:
        when = (r["started"] or "")[:10]
        print(
            f"  {when:<11} {r['surface']:<12} {r['title'][:58]:<58} "
            f"{terminal.human(r['tokens']):>9} {terminal.money(r['cost']):>9}"
        )
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vibewatt",
        description="Token usage, cost and plan utilization for Claude Code, Cowork and other agents.",
    )
    p.add_argument("--version", action="version", version=f"vibewatt {__version__}")
    p.add_argument(
        "command",
        nargs="?",
        default="report",
        choices=[
            "report",
            "serve",
            "blocks",
            "statusline",
            "status",
            "quota",
            "json",
            "csv",
            "doctor",
            "harvest",
            "sync",
            "sessions",
            "export",
            "import",
        ],
        help="report (default), serve, doctor, harvest, sync, sessions, "
        "blocks, statusline, status, quota, json, csv",
    )
    p.add_argument("archive", nargs="?", help="import: .vwx archive")
    p.add_argument("--account", help="OAuth account UUID or unknown")
    p.add_argument(
        "--include-titles", action="store_true", help="export: include session titles"
    )
    p.add_argument(
        "--json", action="store_true", help="status/quota: versioned JSON output"
    )
    p.add_argument(
        "--source",
        choices=["all", "claude", CLAUDE_CODE, COWORK, CODEX, COPILOT, ANTIGRAVITY],
        default="all",
        help="all: every provider; claude: every Claude surface",
    )
    p.add_argument(
        "--since",
        metavar="DATE",
        help="YYYY-MM-DD, 7d, 2w, this-month or last-month (report timezone)",
    )
    p.add_argument("--days", type=int, metavar="N")
    p.add_argument("--tz", help="timezone for day buckets (default: local)")
    p.add_argument(
        "--day-start-hour",
        type=int,
        metavar="H",
        help="hour (0-23) a day starts at, so late nights count as one day",
    )
    p.add_argument("--weeks", type=int, help="heatmap width in weeks")
    p.add_argument(
        "--session-hours", type=int, help="rate-limit window length (default 5)"
    )
    p.add_argument(
        "--no-sidechains", action="store_true", help="exclude subagent turns"
    )
    p.add_argument("--by-project", action="store_true")
    p.add_argument(
        "--mask-projects", action="store_true", help="pseudonymise project names"
    )
    p.add_argument(
        "--no-quota", action="store_true", help="skip the account-level lookup"
    )
    p.add_argument("--offline", action="store_true", help="never fetch pricing")
    p.add_argument(
        "--out", metavar="PATH", help="write csv/json here instead of stdout"
    )
    p.add_argument("--host", default="127.0.0.1", help="serve: bind address")
    p.add_argument("--port", type=int, default=8777, help="serve: port")
    p.add_argument(
        "--no-browser", action="store_true", help="serve: do not open a browser"
    )
    p.add_argument("--no-color", action="store_true")
    p.add_argument(
        "--plan",
        type=float,
        metavar="USD",
        help="your monthly plan price, to show API-equivalent savings",
    )
    p.add_argument(
        "--file", metavar="PATH", help="harvest: a session listing JSON to ingest"
    )
    return p


def _probe_host(host: str) -> str:
    """A connectable spelling of `host`: a wildcard bind answers on loopback, and
    0.0.0.0 is not a valid connect target on Windows."""
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _port_in_use(host: str, port: int) -> bool:
    """True when something already answers on host:port (issue #120).

    A connect, deliberately, not a bind probe. uvicorn binds with SO_REUSEADDR on
    every platform, and on Windows that option can be granted over an address
    another process is already listening on. A probe that set it therefore called a
    busy port free: `serve` carried on and the real bind then died with WinError
    10048, with no --port hint (PR #134 review). A completed connect is evidence on
    every platform, IPv4 and IPv6 alike.
    """
    for family, socktype, proto, _, sockaddr in socket.getaddrinfo(
        _probe_host(host), port, type=socket.SOCK_STREAM
    ):
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(0.25)
            if sock.connect_ex(sockaddr) == 0:
                return True
        except OSError:
            continue  # an address we cannot reach is not evidence of a listener
        finally:
            sock.close()
    return False


def _bind_listeners(host: str, port: int) -> list[socket.socket] | None:
    """Claim the listening sockets, or None when the port is already taken.

    Bound here and handed to uvicorn so the check and the server are ONE bind with
    the same options, that option included. Only EADDRINUSE means "taken": every
    other bind error is re-raised, because a genuine startup failure must not be
    dressed up as a port conflict (PR #134 review).
    """
    listeners: list[socket.socket] = []
    for family, socktype, proto, _, sockaddr in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        sock = socket.socket(family, socktype, proto)
        # uvicorn's own option, so this is the claim the server would have made.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # asyncio's create_server — the path uvicorn took before the socket was
        # supplied — turns dual-stack off for IPv6 sockets, which is why
        # `--host ::` never answered IPv4-mapped connections. create_server(sock=)
        # keeps whatever the socket already carries, so without this the handover
        # would quietly widen the interfaces a given host serves (PR #134 review).
        if family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6"):
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            sock.bind(sockaddr)
        except OSError as exc:
            sock.close()
            for opened in listeners:
                opened.close()
            if exc.errno == errno.EADDRINUSE:
                return None
            raise
        listeners.append(sock)
    return listeners


def _port_conflict_exit(host: str, port: int) -> int:
    print(
        f"vibewatt: {host}:{port} is already in use; choose another port with --port.",
        file=sys.stderr,
    )
    return 1


def _utf8_streams() -> None:
    # Windows gives a redirected stdout the ANSI code page, which cannot encode
    # the heatmap's block glyph, so every `vibewatt > file` crashed (A-057).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if stream.isatty():
            reconfigure(errors="replace")
        else:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    original = sys.stdout
    output = PipeOutput(original)
    sys.stdout = output
    try:
        try:
            code = _run_cli(argv)
        except StdoutClosed as exc:
            return exc.code
        except BaseException as exc:
            try:
                output.flush()
            except StdoutClosed:
                # Help/version exit through argparse; actual errors keep their
                # original exception and exit status even when stdout is closed.
                if isinstance(exc, SystemExit) and exc.code in (None, 0):
                    return 1
            raise
        try:
            output.flush()
        except StdoutClosed:
            return code or 1
        return code
    finally:
        sys.stdout = original


def _run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = configmod.load()
    from . import identity, store

    if args.archive and args.command != "import":
        raise SystemExit("An archive path is accepted only by import")
    try:
        with identity.scope(
            args.account or cfg.get("account") or identity.account_id()
        ):
            return _main(args, cfg)
    except store.UnsupportedSchemaError as exc:
        print(f"vibewatt: {exc}", file=sys.stderr)
        return 2


def _main(args, cfg) -> int:
    if args.tz:
        cfg["timezone"] = args.tz
    if args.day_start_hour is not None:
        cfg["day_start_hour"] = args.day_start_hour
    if args.weeks:
        cfg["weeks"] = args.weeks
    if args.session_hours:
        cfg["session_length_hours"] = args.session_hours
    if args.no_sidechains:
        cfg["include_sidechains"] = False
    if args.no_quota:
        cfg["quota"] = False
    if args.offline:
        cfg["offline"] = True
    if args.mask_projects:
        cfg["mask_projects"] = True
    if args.plan:
        cfg["plan_usd_per_month"] = args.plan

    if args.command in ("export", "import"):
        from . import store, transfer

        try:
            if args.command == "export":
                if not args.out:
                    raise ValueError("export requires --out file.vwx")
                with store.connect() as conn:
                    result = transfer.export_file(
                        conn, Path(args.out), include_titles=args.include_titles
                    )
            else:
                if not args.archive:
                    raise ValueError("import requires file.vwx")
                result = transfer.import_file(Path(args.archive), cfg, report_zone(cfg))
            print(json.dumps(result))
            return 0
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if args.command == "serve":
        import threading
        import webbrowser

        import uvicorn
        from uvicorn.config import STARTUP_FAILURE

        from .api import create_app
        from .api.security import is_loopback

        if _port_in_use(args.host, args.port):
            return _port_conflict_exit(args.host, args.port)
        listeners = _bind_listeners(args.host, args.port)
        if listeners is None:
            return _port_conflict_exit(args.host, args.port)

        local = is_loopback(args.host)
        from . import identity

        cfg["account"] = identity.selected_account()
        app = create_app(cfg, extra_hosts=None if local else {args.host})
        shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        url = f"http://{shown}:{args.port}/"
        print(f"  vibewatt dashboard on {url}")
        if not local:
            # Any non-loopback bind, not just 0.0.0.0 (A-103).
            print(
                f"  WARNING: bound to {args.host}. Anyone who can reach this port can read your"
                " usage and session titles, trigger syncs and spend API credit on summaries.",
                file=sys.stderr,
            )
        print("  ctrl-c to stop")
        if not args.no_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        # Serve on the sockets claimed above, so the server cannot bind differently
        # from the check, and no bind failure can land after the browser is told to
        # open (PR #134 review).
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        try:
            server.run(sockets=listeners)
        except KeyboardInterrupt:
            # uvicorn re-raises the signal it captured once it has shut down
            # gracefully, and uvicorn.run() swallows it. Serving through Server
            # directly loses that courtesy unless it is repeated here: ctrl-c
            # printed a traceback instead of stopping quietly.
            pass
        if not server.started:
            # uvicorn.run() exits with the same code when the server never came up,
            # so a startup failure stays loud instead of looking like a clean stop.
            return STARTUP_FAILURE
        return 0

    tz = report_zone(cfg)

    if args.command == "doctor":
        from .doctor import run as doctor_run

        return doctor_run(cfg, tz)

    if args.command == "harvest":
        return harvest(args, cfg, tz)

    if args.command == "sync":
        return sync(args, cfg, tz)

    if args.command == "sessions":
        return sessions_report(args, cfg, tz)

    if args.command == "statusline":
        from . import identity

        if identity.selected_account() != identity.account_id():
            raise SystemExit("statusline input belongs to the current local account")
        raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        print(statusline(cfg, tz, raw))
        return 0

    if args.command in ("status", "quota"):
        from .agent_output import run

        return run(args, cfg, tz)

    pricing.refresh(offline=cfg.get("offline", False))

    cutoff = None
    if args.days:
        cutoff = (datetime.now(tz) - timedelta(days=args.days)).date()
    if args.since:
        try:
            cutoff = _parse_since_date(args.since, tz)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    report, q, quota_note, duplicates, files = build_report(
        cfg, tz, source=args.source, date_from=cutoff, refresh=True
    )

    if args.command == "json":
        payload = serialize(report)
        text = json.dumps(payload, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(text)
        return 0

    if args.command == "csv":
        text = to_csv(report)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            sys.stdout.write(text)
        return 0

    color = terminal.use_color() and not args.no_color

    if args.command == "blocks":
        if not report.blocks:
            print("no activity found")
            return 0
        for block in report.blocks[-12:]:
            flag = " <- active" if block.is_active else ""
            print(
                f"  {block.start:%Y-%m-%d %H:%M} - {block.end:%H:%M}  "
                f"{terminal.human(block.bucket.total_tokens):>8} tok  "
                f"{terminal.money(block.bucket.cost):>9}  "
                f"{len(block.models)} model(s){flag}"
            )
        return 0

    if not files and not report.by_day:
        hints = {
            CLAUDE_CODE: "  Claude Code: ~/.claude/projects (override with CLAUDE_CONFIG_DIR)",
            COWORK: "  Cowork:      the Claude desktop data dir (override with VIBEWATT_COWORK_DIR)",
            CODEX: "  Codex:       ~/.codex/sessions (override with CODEX_HOME)",
            COPILOT: "  Copilot:     VS Code chatSessions (override with VIBEWATT_VSCODE_USER_DIRS)",
            ANTIGRAVITY: "  Antigravity: ~/.gemini/antigravity*/conversations (override with ANTIGRAVITY_DATA_DIR)",
        }
        wanted = {
            "all": [CLAUDE_CODE, COWORK, CODEX, COPILOT, ANTIGRAVITY],
            "claude": [CLAUDE_CODE, COWORK],
        }.get(args.source, [args.source])
        label = {
            CLAUDE_CODE: "Claude Code",
            COWORK: "Cowork",
            CODEX: "Codex",
            COPILOT: "Copilot",
            ANTIGRAVITY: "Antigravity",
            "claude": "Claude Code or Cowork",
        }.get(args.source, "Claude Code, Cowork, Codex, Copilot or Antigravity")
        print(
            f"No {label} session logs found. Looked in:\n"
            + "\n".join(hints[s] for s in wanted),
            file=sys.stderr,
        )
        return 1

    out = sys.stdout
    bold = terminal.BOLD if color else ""
    reset = terminal.RESET if color else ""
    dim = terminal.DIM if color else ""
    if args.source == CODEX:
        title = "Codex usage (local logs, API-equivalent estimate)"
    elif args.source == COPILOT:
        title = "Copilot usage (local logs, billed credits where recorded)"
    elif args.source == ANTIGRAVITY:
        title = "Antigravity usage (local logs, API-equivalent estimate)"
    elif args.source == "all":
        title = f"All usage ({', '.join(sorted(report.by_source)) or 'no data'})"
    else:
        title = "Claude usage"
    print(f"\n  {bold}{title}{reset}\n", file=out)
    print(terminal.summary(report, color), file=out)
    if q is not None and args.source not in PROVIDER_PLANS:
        print(file=out)
        print(terminal.quota_block(q, color), file=out)
    for provider, note in PROVIDER_PLANS.items():
        if args.source not in ("all", provider) or not cfg.get("quota", True):
            continue
        plan = provider_quota(provider)
        if plan is not None:
            print(file=out)
            print(terminal.quota_block(plan, color, note=note), file=out)
    block_text = terminal.block_block(report, color)
    if block_text:
        print(file=out)
        print(block_text, file=out)
    print(file=out)
    print(
        terminal.heatmap(
            report, weeks=cfg.get("weeks", 53), color=color, today=report.today
        ),
        file=out,
    )
    print(file=out)
    print(terminal.table("model", report.by_model, color), file=out)
    if len(report.by_source) > 1:
        print(file=out)
        print(terminal.table("source", report.by_source, color), file=out)
    if args.by_project:
        print(file=out)
        print(terminal.table("project", report.by_project, color, limit=15), file=out)

    notes = []
    if duplicates:
        notes.append(
            f"{duplicates} repeated content-block rows collapsed into their response"
        )
    if report.restored_days:
        notes.append(
            f"{len(report.restored_days)} day(s) include usage from the imported "
            "history.json"
        )
    if report.unknown_models:
        notes.append("unpriced model(s): " + ", ".join(sorted(report.unknown_models)))
    if q is None and quota_note:
        notes.append(f"plan utilization unavailable - {quota_note}")
    if notes:
        print(file=out)
        for note in notes:
            print(f"  {dim}note: {note}{reset}", file=out)
    print(file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
