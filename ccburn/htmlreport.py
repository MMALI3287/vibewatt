"""Single-file HTML report.

Sequential encoding only: one blue hue, light to dark, with the light end
receding toward the surface. Light and dark steps are chosen per surface rather
than flipped, and every cell carries a hover tooltip so the grid is readable
without reading colour alone.
"""

from __future__ import annotations

import html
import json
from datetime import date, timedelta

from .aggregate import Report
from .terminal import human, money

LIGHT = ["#f0efec", "#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#104281"]
DARK = ["#2f2f2d", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]

_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{color-scheme:light;
  --surface-0:#ffffff;--surface-1:#fcfcfb;--border:#e4e3de;
  --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#84837c;
  --l0:__L0__;--l1:__L1__;--l2:__L2__;--l3:__L3__;--l4:__L4__;--l5:__L5__;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
  --surface-0:#121211;--surface-1:#1a1a19;--border:#34332f;
  --text-primary:#ffffff;--text-secondary:#c3c2b7;--text-muted:#8e8d84;
  --l0:__D0__;--l1:__D1__;--l2:__D2__;--l3:__D3__;--l4:__D4__;--l5:__D5__;}}
:root[data-theme="dark"]{color-scheme:dark;
  --surface-0:#121211;--surface-1:#1a1a19;--border:#34332f;
  --text-primary:#ffffff;--text-secondary:#c3c2b7;--text-muted:#8e8d84;
  --l0:__D0__;--l1:__D1__;--l2:__D2__;--l3:__D3__;--l4:__D4__;--l5:__D5__;}
body{margin:0;background:var(--surface-0);color:var(--text-primary);
  font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.viz{background:var(--surface-0);color:var(--text-primary);padding:32px 16px 56px}
.wrap{max-width:1040px;margin:0 auto}
h1{font-size:19px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--text-muted);font-size:13px;margin:0 0 26px;max-width:62ch}
/* Four columns keeps the eight tiles to exactly two full rows, so the grid
   never ends on a half-empty band. */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;
  background:var(--border);border:1px solid var(--border);border-radius:10px;
  overflow:hidden;margin:0 0 28px}
.kpi{background:var(--surface-1);padding:13px 15px;margin:0}
.kpi dt{color:var(--text-muted);font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;margin:0 0 5px}
.kpi dd{margin:0;font-size:20px;font-weight:600;letter-spacing:-.02em;
  color:var(--text-primary);font-variant-numeric:tabular-nums}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:18px;margin-bottom:22px}
.card h2{font-size:13px;font-weight:600;margin:0 0 14px;color:var(--text-secondary)}
.scroll{overflow-x:auto;padding-bottom:4px}
.grid{display:grid;grid-auto-flow:column;grid-template-rows:repeat(7,12px);gap:2px;width:max-content}
.cell{width:12px;height:12px;border-radius:2px;background:var(--l0)}
.cell[data-l="1"]{background:var(--l1)}.cell[data-l="2"]{background:var(--l2)}
.cell[data-l="3"]{background:var(--l3)}.cell[data-l="4"]{background:var(--l4)}
.cell[data-l="5"]{background:var(--l5)}
.cell[data-v]{cursor:help}
.months{display:grid;grid-auto-flow:column;gap:2px;width:max-content;margin-bottom:5px;
  color:var(--text-muted);font-size:10.5px;height:13px}
.months span{width:12px;white-space:nowrap}
.legend{display:flex;align-items:center;gap:6px;justify-content:flex-end;
  margin-top:12px;color:var(--text-muted);font-size:11.5px}
.legend i{width:12px;height:12px;border-radius:2px;display:block}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 9px;border-bottom:1px solid var(--border);font-size:13px;
  white-space:nowrap}
th{color:var(--text-muted);font-weight:500;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase}
th:first-child,td:first-child{text-align:left}
td{color:var(--text-primary)}
tbody tr:last-child td{border-bottom:0}
.note{color:var(--text-muted);font-size:12px;margin-top:18px}
.meter{display:grid;grid-template-columns:110px 1fr 58px 130px;gap:10px;align-items:center;
  padding:7px 0;font-size:13px}
.meter .track{height:8px;border-radius:99px;background:var(--border);overflow:hidden}
.meter .fill{height:100%;border-radius:99px}
.meter .pct{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.meter .reset{color:var(--text-muted);font-size:11.5px}
.good{background:#1baf7a}.warn{background:#eda100}.hot{background:#e34948}
.rows{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
.rows div span{display:block;color:var(--text-muted);font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;margin-bottom:3px}
.rows div b{font-size:16px;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.live{display:inline-block;width:7px;height:7px;border-radius:99px;background:#1baf7a;
  margin-right:6px;vertical-align:middle}
.hours{display:grid;grid-template-columns:repeat(24,1fr);gap:2px;margin-top:4px}
.hours i{height:26px;border-radius:2px;background:var(--l0);display:block}
.hourlabels{display:grid;grid-template-columns:repeat(24,1fr);gap:2px;margin-top:4px;
  color:var(--text-muted);font-size:10px;text-align:center}
@media (max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}}
@media (max-width:520px){.kpi dd{font-size:17px}}
"""


def _cells(report: Report, weeks: int = 53) -> tuple[list[str], list[str]]:
    from .terminal import level

    today = date.today()
    end = today + timedelta(days=(6 - today.weekday()))
    start = end - timedelta(weeks=weeks) + timedelta(days=1)
    ceiling = max((b.total_tokens for b in report.by_day.values()), default=0)

    cells: list[str] = []
    months: list[str] = []
    seen: set[tuple[int, int]] = set()
    for w in range(weeks):
        col = start + timedelta(weeks=w)
        key = (col.year, col.month)
        label = ""
        if col.day <= 7 and key not in seen:
            seen.add(key)
            label = col.strftime("%b")
        months.append(f"<span>{label}</span>")
        for weekday in range(7):
            day = start + timedelta(weeks=w, days=weekday)
            if day > today:
                cells.append('<div class="cell" aria-hidden="true"></div>')
                continue
            bucket = report.by_day.get(day)
            total = bucket.total_tokens if bucket else 0
            lvl = level(total, ceiling)
            if not bucket:
                tip = f"{day:%a %d %b %Y} - no activity"
                cells.append(
                    f'<div class="cell" data-l="0" title="{html.escape(tip)}"></div>'
                )
                continue
            models = sorted(
                ((m, b) for (d, m), b in report.by_day_model.items() if d == day),
                key=lambda kv: kv[1].cost,
                reverse=True,
            )
            detail = ", ".join(f"{m.replace('claude-', '')} {money(b.cost)}" for m, b in models)
            tip = (
                f"{day:%a %d %b %Y} - {human(total)} tokens, {money(bucket.cost)}"
                f"{chr(10) + detail if detail else ''}"
            )
            cells.append(
                f'<div class="cell" data-l="{lvl}" data-v="{total}" '
                f'title="{html.escape(tip)}"></div>'
            )
    return cells, months


def _table(title: str, heading: str, buckets: dict) -> str:
    if not buckets:
        return ""
    rows = sorted(buckets.items(), key=lambda kv: kv[1].cost, reverse=True)
    body = "".join(
        "<tr><td>{name}</td><td>{inp}</td><td>{cw}</td><td>{cr}</td>"
        "<td>{out}</td><td>{turns}</td><td>{cost}</td></tr>".format(
            name=html.escape(name),
            inp=human(b.input),
            cw=human(b.cache_5m + b.cache_1h),
            cr=human(b.cache_read),
            out=human(b.output),
            turns=f"{b.turns:,}",
            cost=money(b.cost),
        )
        for name, b in rows
    )
    return (
        f'<div class="card"><h2>{html.escape(heading)}</h2>'
        "<div class='scroll'><table><thead><tr>"
        f"<th>{html.escape(title)}</th><th>input</th><th>cache write</th><th>cache read</th>"
        "<th>output</th><th>responses</th><th>est. cost</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div></div>"
    )


def _quota_html(quota) -> str:
    if quota is None:
        return ""
    rows = []
    for w in quota.windows:
        pct = max(0.0, min(100.0, w.utilization))
        cls = "good" if pct < 75 else ("warn" if pct < 90 else "hot")
        left = ""
        if w.remaining_seconds is not None:
            hours, rem = divmod(int(w.remaining_seconds), 3600)
            left = f"resets in {hours}h {rem // 60:02d}m"
        rows.append(
            f'<div class="meter"><div>{html.escape(w.label)}</div>'
            f'<div class="track"><div class="fill {cls}" style="width:{pct:.1f}%"></div></div>'
            f'<div class="pct">{pct:.0f}%</div><div class="reset">{html.escape(left)}</div></div>'
        )
    return (
        '<div class="card"><h2>Plan utilization</h2>'
        + "".join(rows)
        + '<p class="note">Account-wide, so this includes Claude Code on the web, '
          'Cowork remote sessions and claude.ai chat &mdash; none of which write '
          f'local logs. Read via {html.escape(quota.source)}.</p></div>'
    )


def _block_html(report) -> str:
    active = report.active_block
    if active is None:
        return ""
    tokens, cost = active.project_to_end()
    cells = [
        ("window", f"{active.start:%H:%M}&ndash;{active.end:%H:%M}"),
        ("spent so far", money(active.bucket.cost)),
        ("burn rate", f"{human(active.tokens_per_minute)} tok/min"),
        ("projected at close", money(cost)),
    ]
    body = "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in cells)
    return (
        '<div class="card"><h2><i class="live"></i>Current rate-limit window</h2>'
        f'<div class="rows">{body}</div></div>'
    )


def _hours_html(report) -> str:
    if not report.by_hour:
        return ""
    from .terminal import level

    peak = max((b.total_tokens for b in report.by_hour.values()), default=0)
    cells = []
    for hour in range(24):
        bucket = report.by_hour.get(hour)
        total = bucket.total_tokens if bucket else 0
        lvl = level(total, peak)
        tip = f"{hour:02d}:00 - {hour:02d}:59 | {human(total)} tokens"
        cells.append(f'<i data-l="{lvl}" style="background:var(--l{lvl})" title="{html.escape(tip)}"></i>')
    labels = "".join(f"<span>{h if h % 3 == 0 else ''}</span>" for h in range(24))
    return (
        '<div class="card"><h2>Time of day</h2>'
        f'<div class="hours">{"".join(cells)}</div>'
        f'<div class="hourlabels">{labels}</div></div>'
    )


def build_html(report: Report, weeks: int = 53, quota=None) -> str:
    cells, months = _cells(report, weeks)
    current, longest = report.streaks()
    peak = report.peak_day()
    b = report.total

    kpis = [
        ("input tokens", human(b.input)),
        ("cache write", human(b.cache_5m + b.cache_1h)),
        ("cache read", human(b.cache_read)),
        ("output tokens", human(b.output)),
        ("est. cost", money(b.cost)),
        ("active days", f"{len(report.by_day):,}"),
        ("peak day", f"{peak[0]:%d %b}" if peak else "-"),
        ("streak", f"{current}d"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd></div>'
        for k, v in kpis
    )
    legend = "".join(f'<i style="background:var(--l{i})"></i>' for i in range(6))
    css = _CSS
    for i in range(6):
        css = css.replace(f"__L{i}__", LIGHT[i]).replace(f"__D{i}__", DARK[i])
    notes = ""
    if report.unknown_models:
        names = html.escape(", ".join(sorted(report.unknown_models)))
        notes = (
            f'<p class="note">Tokens counted but not priced for unknown model(s): {names}.</p>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Claude usage</title><style>{css}</style></head>
<body><div class="viz"><div class="wrap">
<h1>Claude usage</h1>
<p class="sub">Claude Code and Cowork, from local session logs. Cost is estimated at
list API rates, not what a subscription bills.</p>
<dl class="kpis">{kpi_html}</dl>
{_quota_html(quota)}
{_block_html(report)}
<div class="card"><h2>Daily activity, by total tokens</h2>
<div class="scroll"><div class="months">{''.join(months)}</div>
<div class="grid" role="img" aria-label="Calendar heatmap of daily token usage">{''.join(cells)}</div></div>
<div class="legend"><span>Less</span>{legend}<span>More</span></div></div>
{_hours_html(report)}
{_table('model', 'Usage by model', report.by_model)}
{_table('source', 'Usage by source', report.by_source) if len(report.by_source) > 1 else ''}
{_table('project', 'Usage by project', dict(sorted(report.by_project.items(), key=lambda kv: kv[1].cost, reverse=True)[:15]))}
{notes}
</div></div>
<script>
/* The newest weeks matter most, so open the grid scrolled to its right edge. */
for (const el of document.querySelectorAll('.card .scroll')) el.scrollLeft = el.scrollWidth;
</script>
</body></html>"""


def write_html(report: Report, path: str, weeks: int = 53, quota=None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_html(report, weeks, quota))
