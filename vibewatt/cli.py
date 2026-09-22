"""Command line entry point."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone

from . import config as configmod
from . import history, pricing, quota, terminal
from .aggregate import build
from .ingest import discover
from .sources import CLAUDE_CODE, COWORK, load


def resolve_tz(name: str | None):
    if not name or name == "local":
        return datetime.now().astimezone().tzinfo
    if name == "utc":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"unknown timezone {name!r}: {exc}")


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
        "cost_usd": round(b.cost, 6),
    }


def serialize(report) -> dict:
    active = report.active_block
    payload = {
        "total": _bucket_dict(report.total),
        "by_day": {str(d): _bucket_dict(b) for d, b in sorted(report.by_day.items())},
        "by_model": {m: _bucket_dict(b) for m, b in report.by_model.items()},
        "by_source": {s: _bucket_dict(b) for s, b in report.by_source.items()},
        "by_project": {p: _bucket_dict(b) for p, b in report.by_project.items()},
        "by_day_model": {f"{d}|{m}": _bucket_dict(b)
                         for (d, m), b in sorted(report.by_day_model.items())},
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


def to_csv(report) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["date", "model", "responses", "input", "cache_write_5m",
                     "cache_write_1h", "cache_read", "output", "cost_usd"])
    for (day, model), b in sorted(report.by_day_model.items()):
        writer.writerow([day, model, b.turns, b.input, b.cache_5m,
                         b.cache_1h, b.cache_read, b.output, f"{b.cost:.6f}"])
    return out.getvalue()


def mask_projects(report) -> None:
    """Replace project names with stable pseudonyms, for sharing a screenshot."""
    renamed = {}
    for index, (name, bucket) in enumerate(
        sorted(report.by_project.items(), key=lambda kv: kv[1].cost, reverse=True), 1
    ):
        renamed[f"project {index}"] = bucket
    report.by_project.clear()
    report.by_project.update(renamed)


def apply_aliases(report, aliases: dict) -> None:
    if not aliases:
        return
    from .aggregate import Bucket

    merged: dict = {}
    for name, bucket in report.by_project.items():
        label = aliases.get(name, name)
        if label in merged:
            existing = merged[label]
            for f in ("turns", "input", "cache_5m", "cache_1h", "cache_read",
                      "output", "thinking", "web_searches", "cost"):
                setattr(existing, f, getattr(existing, f) + getattr(bucket, f))
        else:
            merged[label] = bucket
    report.by_project.clear()
    report.by_project.update(merged)


def statusline(report, q) -> str:
    """One compact line for a Claude Code statusLine hook or a tmux bar."""
    parts = []
    if q is not None:
        for w in q.windows[:2]:
            parts.append(f"{w.label} {w.utilization:.0f}%")
    active = report.active_block
    if active is not None:
        parts.append(f"{terminal.money(active.bucket.cost)} this window")
        parts.append(f"{terminal.human(active.tokens_per_minute)}/min")
    parts.append(f"{terminal.money(report.month_to_date().cost)} MTD")
    return "  |  ".join(parts)


def build_report(cfg, tz, *, source="all", date_from=None, date_to=None,
                  project=None, model=None):
    """The discover -> load -> aggregate -> history -> quota pipeline.

    Shared by the CLI and the API so filtering logic lives in exactly one
    place. `date_from`/`date_to` are inclusive `date` objects; `project` and
    `model` match a turn's exact project/model string.
    """
    files = discover(cfg)
    if source != "all":
        files = [(s, p) for s, p in files if s == source]
    turns, duplicates = load(files)
    if date_from:
        turns = [t for t in turns if t.ts.astimezone(tz).date() >= date_from]
    if date_to:
        turns = [t for t in turns if t.ts.astimezone(tz).date() <= date_to]
    if project:
        turns = [t for t in turns if t.project == project]
    if model:
        turns = [t for t in turns if t.model == model]

    report = build(
        turns, tz=tz,
        include_sidechains=cfg.get("include_sidechains", True),
        overrides=cfg.get("pricing_overrides"),
        session_hours=cfg.get("session_length_hours", 5),
    )

    # History is one rollup across every source and project: restoring it into a
    # filtered report re-adds the rows the filter removed, and merging a filtered
    # report would overwrite the rollup with a partial view.
    unfiltered = source == "all" and not (date_from or date_to or project or model)
    if cfg.get("history", True) and unfiltered:
        history.merge(report)
        history.restore(report)

    apply_aliases(report, cfg.get("project_aliases") or {})
    if cfg.get("mask_projects"):
        mask_projects(report)

    q, quota_note = quota.read(cfg)
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
            file=sys.stderr)
        return 2
    try:
        with open(args.file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read {args.file}: {exc}")
    # Accept the tool's envelope or a bare list.
    if isinstance(payload, dict) and "ccr" in payload:
        payload = payload["ccr"]
    with store.connect() as conn:
        written, skipped = store.upsert_cloud_sessions(conn, payload)
        info = store.summary(conn)
    print(f"  harvested {written} session(s), skipped {skipped} with no usage block")
    for row in info["cloud_by_surface"]:
        print(f"    {row['surface']:<12} {row['n']:>4} session(s)   ${row['cost'] or 0:,.2f}")
    return 0


def sync(args, cfg, tz) -> int:
    """Parse changed local logs into the store so later queries do not re-read them."""
    from . import store
    from .aggregate import cost_of

    files = discover(cfg)
    overrides = cfg.get("pricing_overrides")
    with store.connect() as conn:
        result = store.sync_files(conn, files, tz, lambda t: cost_of(t, overrides))
        info = store.summary(conn)
    print(f"  parsed {result.parsed} changed file(s), skipped {result.skipped} unchanged, "
          f"{len(files)} total")
    print(f"  synced {result.turns} response(s), "
          f"{result.duplicates} content-block repeats collapsed")
    print(f"  recovered {result.prompts} prompt title(s)")
    t = info["turns"]
    print(f"  store now holds {t['n']:,} response(s)  {t['lo']} .. {t['hi']}  "
          f"${t['cost'] or 0:,.2f}")
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
    print(f"\n  {'when':<11} {'surface':<12} {'what you worked on':<58} "
          f"{'tokens':>9} {'cost':>9}")
    print("  " + "-" * 103)
    for r in rows:
        when = (r["started"] or "")[:10]
        print(f"  {when:<11} {r['surface']:<12} {r['title'][:58]:<58} "
              f"{terminal.human(r['tokens']):>9} {terminal.money(r['cost']):>9}")
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vibewatt",
        description="Token usage, cost and plan utilization for Claude Code and Cowork.",
    )
    p.add_argument("command", nargs="?", default="report",
                   choices=["report", "serve", "blocks", "statusline", "json", "csv",
                            "html", "doctor", "harvest", "sync", "sessions"],
                   help="report (default), serve, doctor, harvest, sync, sessions, "
                        "blocks, statusline, json, csv, html")
    p.add_argument("--source", choices=[CLAUDE_CODE, COWORK, "all"], default="all")
    p.add_argument("--since", metavar="YYYY-MM-DD")
    p.add_argument("--days", type=int, metavar="N")
    p.add_argument("--tz", help="timezone for day buckets (default: local)")
    p.add_argument("--weeks", type=int, help="heatmap width in weeks")
    p.add_argument("--session-hours", type=int, help="rate-limit window length (default 5)")
    p.add_argument("--no-sidechains", action="store_true", help="exclude subagent turns")
    p.add_argument("--by-project", action="store_true")
    p.add_argument("--mask-projects", action="store_true", help="pseudonymise project names")
    p.add_argument("--no-quota", action="store_true", help="skip the account-level lookup")
    p.add_argument("--no-history", action="store_true", help="do not read or write stored history")
    p.add_argument("--offline", action="store_true", help="never fetch pricing")
    p.add_argument("--out", metavar="PATH", help="write html/csv/json here instead of stdout")
    p.add_argument("--host", default="127.0.0.1", help="serve: bind address")
    p.add_argument("--port", type=int, default=8777, help="serve: port")
    p.add_argument("--refresh", type=int, default=30, help="serve: refresh seconds (0 disables)")
    p.add_argument("--no-browser", action="store_true", help="serve: do not open a browser")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--plan", type=float, metavar="USD",
                   help="your monthly plan price, to show API-equivalent savings")
    p.add_argument("--file", metavar="PATH",
                   help="harvest: a session listing JSON to ingest")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = configmod.load()
    if args.tz:
        cfg["timezone"] = args.tz
    if args.weeks:
        cfg["weeks"] = args.weeks
    if args.session_hours:
        cfg["session_length_hours"] = args.session_hours
    if args.no_sidechains:
        cfg["include_sidechains"] = False
    if args.no_quota:
        cfg["quota"] = False
    if args.no_history:
        cfg["history"] = False
    if args.offline:
        cfg["offline"] = True
    if args.mask_projects:
        cfg["mask_projects"] = True
    if args.plan:
        cfg["plan_usd_per_month"] = args.plan

    if args.command == "serve":
        import threading
        import webbrowser

        import uvicorn

        from .api import create_app

        app = create_app(cfg)
        shown = args.host if args.host != "0.0.0.0" else "127.0.0.1"
        url = f"http://{shown}:{args.port}/"
        print(f"  vibewatt dashboard on {url}")
        print(f"  JSON at {url}api/usage")
        if args.host == "0.0.0.0":
            print("  bound to all interfaces - anyone who can reach this port sees your usage")
        print("  ctrl-c to stop")
        if not args.no_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        return 0

    tz = resolve_tz(cfg.get("timezone"))

    if args.command == "doctor":
        from .doctor import run as doctor_run

        return doctor_run(cfg, tz)

    if args.command == "harvest":
        return harvest(args, cfg, tz)

    if args.command == "sync":
        return sync(args, cfg, tz)

    if args.command == "sessions":
        return sessions_report(args, cfg, tz)

    pricing.refresh(offline=cfg.get("offline", False))

    cutoff = None
    if args.days:
        cutoff = (datetime.now(tz) - timedelta(days=args.days)).date()
    if args.since:
        try:
            cutoff = datetime.strptime(args.since, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit(f"--since expects YYYY-MM-DD, got {args.since!r}")

    report, q, quota_note, duplicates, files = build_report(
        cfg, tz, source=args.source, date_from=cutoff)

    if args.command == "json":
        payload = serialize(report)
        text = json.dumps(payload, indent=2)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text + "\n")
            print(f"wrote {args.out}")
        else:
            print(text)
        return 0

    if args.command == "csv":
        text = to_csv(report)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text)
            print(f"wrote {args.out}")
        else:
            sys.stdout.write(text)
        return 0

    if args.command == "html":
        from .ui import build_dataset, write

        target = args.out or "vibewatt-report.html"
        write(build_dataset(report, cfg, quota=q, duplicates=duplicates), target)
        print(f"wrote {target}")
        return 0

    if args.command == "statusline":
        print(statusline(report, q))
        return 0

    color = terminal.use_color() and not args.no_color

    if args.command == "blocks":
        if not report.blocks:
            print("no activity found")
            return 0
        for block in report.blocks[-12:]:
            flag = " <- active" if block.is_active else ""
            print(f"  {block.start:%Y-%m-%d %H:%M} - {block.end:%H:%M}  "
                  f"{terminal.human(block.bucket.total_tokens):>8} tok  "
                  f"{terminal.money(block.bucket.cost):>9}  "
                  f"{len(block.models)} model(s){flag}")
        return 0

    if not files and not report.by_day:
        hints = {
            CLAUDE_CODE: "  Claude Code: ~/.claude/projects (override with CLAUDE_CONFIG_DIR)",
            COWORK: "  Cowork:      the Claude desktop data dir (override with VIBEWATT_COWORK_DIR)",
        }
        wanted = [args.source] if args.source != "all" else list(hints)
        label = "Cowork" if args.source == COWORK else (
            "Claude Code" if args.source == CLAUDE_CODE else "Claude Code or Cowork")
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
    print(f"\n  {bold}Claude usage{reset}\n", file=out)
    print(terminal.summary(report, color), file=out)
    if q is not None:
        print(file=out)
        print(terminal.quota_block(q, color), file=out)
    block_text = terminal.block_block(report, color)
    if block_text:
        print(file=out)
        print(block_text, file=out)
    print(file=out)
    print(terminal.heatmap(report, weeks=cfg.get("weeks", 53), color=color), file=out)
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
        notes.append(f"{duplicates} repeated content-block rows collapsed into their response")
    if report.restored_days:
        notes.append(f"{len(report.restored_days)} day(s) restored from stored history")
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
