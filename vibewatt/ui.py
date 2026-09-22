"""The interactive report.

One page, driven by an embedded dataset rather than server-rendered HTML, so the
same file works three ways: opened from disk with no server, served live by
``vibewatt serve``, and filtered entirely in the browser with no round trip.

Filters apply to everything derived from local logs. They deliberately do NOT
apply to plan utilization, which is an account-wide figure that cannot be sliced
by project or source; that panel says so on its face.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

LIGHT = ["#f0efec", "#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#104281"]
DARK = ["#2f2f2d", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]


def build_dataset(report, cfg: dict, quota=None, duplicates: int = 0) -> dict:
    """Flatten the report into index-encoded rows the page can filter cheaply."""
    sources, projects, models, days = [], [], [], []
    s_idx, p_idx, m_idx, d_idx = {}, {}, {}, {}

    def intern(value, store, index):
        if value not in index:
            index[value] = len(store)
            store.append(value)
        return index[value]

    rows = []
    for (day, source, project, model), b in sorted(report.by_cell.items()):
        rows.append([
            intern(str(day), days, d_idx),
            intern(source, sources, s_idx),
            intern(project, projects, p_idx),
            intern(model, models, m_idx),
            b.input, b.cache_5m, b.cache_1h, b.cache_read, b.output,
            b.turns, round(b.cost, 6),
        ])

    # Restored days carry no project or source breakdown; keep them separate so
    # totals stay right without inventing a project name for them.
    restored = []
    for (day, model), b in sorted(report.by_day_model.items()):
        if day in report.restored_days:
            restored.append([str(day), model, b.total_tokens, b.turns, round(b.cost, 6)])

    active = report.active_block
    block = None
    if active is not None:
        tokens, cost = active.project_to_end()
        block = {
            "start": active.start.isoformat(),
            "end": active.end.isoformat(),
            "tokens": active.bucket.total_tokens,
            "cost": round(active.bucket.cost, 6),
            "rate": round(active.tokens_per_minute, 1),
            "projected_tokens": tokens,
            "projected_cost": round(cost, 6),
        }

    q = None
    if quota is not None:
        q = {
            "source": quota.source,
            "windows": [
                {"label": w.label, "utilization": w.utilization,
                 "remaining": w.remaining_seconds}
                for w in quota.windows
            ],
        }

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "today": str(report.today or date.today()),
        "timezone": str(cfg.get("timezone")),
        "days": days, "sources": sources, "projects": projects, "models": models,
        "rows": rows,
        "restored": restored,
        "block": block,
        "quota": q,
        "plan_usd": cfg.get("plan_usd_per_month"),
        "budget_usd": cfg.get("monthly_budget_usd"),
        "metric": cfg.get("heatmap_metric", "cost"),
        "subagent": {
            "tokens": report.subagent.total_tokens,
            "cost": round(report.subagent.cost, 6),
            "responses": report.subagent.turns,
        },
        # [hour, cost, tokens] - not filterable, the row grain is per-day
        "hours": [[h, round(report.by_hour[h].cost, 6), report.by_hour[h].total_tokens]
                  for h in range(24) if h in report.by_hour] or None,
        "duplicates": duplicates,
        "unknown_models": sorted(report.unknown_models),
        "sessions": len(report.sessions),
    }


_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{color-scheme:light;
 --s0:#ffffff;--s1:#fcfcfb;--s2:#f5f4f1;--bd:#e4e3de;
 --tp:#0b0b0b;--ts:#52514e;--tm:#84837c;--accent:#2a78d6;
 --l0:__L0__;--l1:__L1__;--l2:__L2__;--l3:__L3__;--l4:__L4__;--l5:__L5__}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --s0:#121211;--s1:#1a1a19;--s2:#232321;--bd:#34332f;
 --tp:#ffffff;--ts:#c3c2b7;--tm:#8e8d84;--accent:#3987e5;
 --l0:__D0__;--l1:__D1__;--l2:__D2__;--l3:__D3__;--l4:__D4__;--l5:__D5__}}
:root[data-theme="dark"]{color-scheme:dark;
 --s0:#121211;--s1:#1a1a19;--s2:#232321;--bd:#34332f;
 --tp:#ffffff;--ts:#c3c2b7;--tm:#8e8d84;--accent:#3987e5;
 --l0:__D0__;--l1:__D1__;--l2:__D2__;--l3:__D3__;--l4:__D4__;--l5:__D5__}
body{margin:0;background:var(--s0);color:var(--tp);font:14px/1.5 ui-sans-serif,system-ui,
 -apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1160px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:19px;margin:0;letter-spacing:-.01em}
.sub{color:var(--tm);font-size:12.5px;margin:3px 0 20px}
.card{background:var(--s1);border:1px solid var(--bd);border-radius:11px;padding:17px;margin-bottom:18px}
.card h2{font-size:12.5px;font-weight:600;margin:0 0 13px;color:var(--ts);
 display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.tag{font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--tm);
 border:1px solid var(--bd);border-radius:99px;padding:2px 8px;font-weight:500}
/* filters */
.filters{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin-bottom:18px}
.chips{display:inline-flex;background:var(--s2);border:1px solid var(--bd);border-radius:9px;
 padding:2px;gap:2px}
.chips button{border:0;background:transparent;color:var(--ts);font:inherit;font-size:12.5px;
 padding:5px 11px;border-radius:7px;cursor:pointer}
.chips button[aria-pressed="true"]{background:var(--s0);color:var(--tp);font-weight:600;
 box-shadow:0 1px 2px rgba(0,0,0,.08)}
select,.reset{background:var(--s2);border:1px solid var(--bd);color:var(--tp);font:inherit;
 font-size:12.5px;padding:6px 10px;border-radius:9px;cursor:pointer;max-width:230px}
.reset{color:var(--ts)}
label.f{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--tm);
 letter-spacing:.05em;text-transform:uppercase}
/* kpis */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--bd);
 border:1px solid var(--bd);border-radius:11px;overflow:hidden;margin-bottom:18px}
.kpi{background:var(--s1);padding:12px 14px}
.kpi dt{color:var(--tm);font-size:10px;letter-spacing:.07em;text-transform:uppercase;margin:0 0 4px}
.kpi dd{margin:0;font-size:19px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi small{display:block;color:var(--tm);font-size:11px;font-weight:400;margin-top:2px}
/* meters */
.meter{display:grid;grid-template-columns:130px 1fr 52px 120px;gap:10px;align-items:center;
 padding:6px 0;font-size:13px}
.track{height:8px;border-radius:99px;background:var(--bd);overflow:hidden}
.fill{height:100%;border-radius:99px;transition:width .3s}
.pct{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.reset-in{color:var(--tm);font-size:11px}
.good{background:#1baf7a}.warn{background:#eda100}.hot{background:#e34948}
/* heatmap */
.scroll{overflow-x:auto;padding-bottom:5px}
.grid{display:grid;grid-auto-flow:column;grid-template-rows:repeat(7,12px);gap:2px;width:max-content}
.cell{width:12px;height:12px;border-radius:2px;background:var(--l0)}
.months{display:grid;grid-auto-flow:column;gap:2px;width:max-content;margin-bottom:5px;
 color:var(--tm);font-size:10px;height:12px}
.months span{width:12px;white-space:nowrap}
.legend{display:flex;align-items:center;gap:6px;justify-content:flex-end;margin-top:11px;
 color:var(--tm);font-size:11px}
.legend i{width:12px;height:12px;border-radius:2px;display:block}
.hours{display:grid;grid-template-columns:repeat(24,1fr);gap:2px}
.hours i{height:24px;border-radius:2px;display:block;background:var(--l0)}
.hourlab{display:grid;grid-template-columns:repeat(24,1fr);gap:2px;margin-top:4px;
 color:var(--tm);font-size:9.5px;text-align:center}
/* tables */
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 9px;border-bottom:1px solid var(--bd);font-size:12.5px;white-space:nowrap}
th{color:var(--tm);font-weight:500;font-size:10px;letter-spacing:.06em;text-transform:uppercase;
 cursor:pointer;user-select:none}
th:first-child,td:first-child{text-align:left}
th[aria-sort]:after{content:"";margin-left:5px;opacity:.5}
th[aria-sort="descending"]:after{content:"\\25BC"}
th[aria-sort="ascending"]:after{content:"\\25B2"}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--s2)}
.note{color:var(--tm);font-size:11.5px;margin:12px 0 0;line-height:1.5}
.empty{color:var(--tm);font-size:13px;padding:18px 0;text-align:center}
.grids{display:grid;grid-template-columns:1fr 1fr;gap:18px}
/* Grid items default to min-width:auto, so a wide table refuses to shrink and
   pushes the whole page sideways. Only the table's own .scroll should scroll. */
.grids>*{min-width:0}
.card{min-width:0}
.kpis>*{min-width:0}
@media (max-width:860px){.kpis{grid-template-columns:repeat(2,1fr)}.grids{grid-template-columns:1fr}
 .meter{grid-template-columns:100px 1fr 46px;}.meter .reset-in{display:none}}
"""


def _js() -> str:
    return r"""
const $ = (s, r) => (r || document).querySelector(s);
const fmt = n => { const a = Math.abs(n);
  if (a >= 1e9) return (n/1e9).toFixed(1)+'B';
  if (a >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (a >= 1e3) return (n/1e3).toFixed(1)+'K';
  return String(Math.round(n)); };
const usd = n => n >= 0.01 || n === 0 ? '$'+n.toLocaleString(undefined,
  {minimumFractionDigits:2, maximumFractionDigits:2}) : '$'+n.toFixed(4);

const I = {DAY:0, SRC:1, PROJ:2, MODEL:3, IN:4, C5:5, C1:6, CR:7, OUT:8, N:9, COST:10};
const METRICS = {
  cost:      {label:'cost',          get: b => b.cost,   fmt: usd},
  total:     {label:'total tokens',  get: b => b.tot,    fmt: fmt},
  output:    {label:'output tokens', get: b => b.out,    fmt: fmt},
  responses: {label:'responses',     get: b => b.n,      fmt: fmt},
};

const state = {range: 'all', source: 'all', project: 'all', model: 'all',
               metric: DATA.metric || 'cost', sort: {}};

function blank() { return {input:0,c5:0,c1:0,cr:0,out:0,n:0,cost:0,tot:0}; }
function addRow(b, r) {
  b.input += r[I.IN]; b.c5 += r[I.C5]; b.c1 += r[I.C1]; b.cr += r[I.CR];
  b.out += r[I.OUT]; b.n += r[I.N]; b.cost += r[I.COST];
  b.tot = b.input + b.c5 + b.c1 + b.cr + b.out;
  return b;
}

function cutoff() {
  if (state.range === 'all') return null;
  const d = new Date(DATA.today + 'T00:00:00');
  d.setDate(d.getDate() - (parseInt(state.range, 10) - 1));
  return d.toISOString().slice(0, 10);
}

function filtered() {
  const from = cutoff();
  return DATA.rows.filter(r =>
    (from === null || DATA.days[r[I.DAY]] >= from) &&
    (state.source === 'all'  || DATA.sources[r[I.SRC]] === state.source) &&
    (state.project === 'all' || DATA.projects[r[I.PROJ]] === state.project) &&
    (state.model === 'all'   || DATA.models[r[I.MODEL]] === state.model));
}

function groupBy(rows, idx, names) {
  const out = new Map();
  for (const r of rows) {
    const k = names[r[idx]];
    if (!out.has(k)) out.set(k, blank());
    addRow(out.get(k), r);
  }
  return out;
}

function byDay(rows) {
  const out = new Map();
  for (const r of rows) {
    const k = DATA.days[r[I.DAY]];
    if (!out.has(k)) out.set(k, blank());
    addRow(out.get(k), r);
  }
  return out;
}

/* ---------- rendering ---------- */

function renderKpis(rows, days) {
  const t = rows.reduce(addRow, blank());
  const cacheServed = t.cr + t.input + t.c5 + t.c1;
  const hit = cacheServed ? (100 * t.cr / cacheServed) : 0;
  let peak = null;
  for (const [d, b] of days) if (!peak || b.tot > peak[1].tot) peak = [d, b];
  const sorted = [...days.keys()].sort();
  let streak = 0;
  if (sorted.length) {
    const last = sorted[sorted.length - 1];
    const gap = Math.round((new Date(DATA.today) - new Date(last)) / 86400000);
    if (gap <= 1) { streak = 1;
      for (let i = sorted.length - 1; i > 0; i--) {
        if ((new Date(sorted[i]) - new Date(sorted[i-1])) === 86400000) streak++; else break;
      } }
  }
  const plan = DATA.plan_usd;
  const items = [
    ['est. API cost', usd(t.cost), plan ? (t.cost/plan).toFixed(0)+'x your $'+plan+' plan' : 'at list API rates'],
    ['total tokens', fmt(t.tot), fmt(t.cr)+' of it cache reads'],
    ['output tokens', fmt(t.out), fmt(t.n)+' responses'],
    ['cache hit rate', hit.toFixed(1)+'%', 'higher is cheaper'],
    ['active days', String(days.size), 'days with local activity'],
    ['peak day', peak ? new Date(peak[0]).toLocaleDateString(undefined,{day:'2-digit',month:'short'}) : '-',
      peak ? METRICS[state.metric].fmt(METRICS[state.metric].get(peak[1])) : ''],
    ['local streak', streak + 'd', 'local logs only'],
    ['projects', String(groupBy(rows, I.PROJ, DATA.projects).size), 'in this selection'],
  ];
  $('#kpis').innerHTML = items.map(([k, v, s]) =>
    `<div class="kpi"><dt>${k}</dt><dd>${v}<small>${s}</small></dd></div>`).join('');
}

function renderHeat(days) {
  const m = METRICS[state.metric];
  let peak = 0;
  for (const b of days.values()) peak = Math.max(peak, m.get(b));
  const today = new Date(DATA.today + 'T00:00:00');
  const end = new Date(today); end.setDate(end.getDate() + (6 - ((today.getDay()+6) % 7)));
  const weeks = 53;
  const start = new Date(end); start.setDate(start.getDate() - weeks * 7 + 1);

  const cells = [], months = [];
  const seen = new Set();
  for (let w = 0; w < weeks; w++) {
    const col = new Date(start); col.setDate(col.getDate() + w * 7);
    const key = col.getFullYear() + '-' + col.getMonth();
    let label = '';
    if (col.getDate() <= 7 && !seen.has(key)) { seen.add(key);
      label = col.toLocaleDateString(undefined, {month:'short'}); }
    months.push(`<span>${label}</span>`);
    for (let d = 0; d < 7; d++) {
      const day = new Date(start); day.setDate(day.getDate() + w*7 + d);
      if (day > today) { cells.push('<div class="cell"></div>'); continue; }
      const iso = day.toISOString().slice(0,10);
      const b = days.get(iso);
      const v = b ? m.get(b) : 0;
      // fourth-root compression: one huge day would otherwise flatten the rest
      const lvl = v <= 0 ? 0 : Math.max(1, Math.min(5, Math.ceil(Math.pow(v/peak, 0.25) * 5)));
      const tip = b ? `${iso}  ${m.fmt(v)} ${m.label}` : `${iso}  no activity`;
      cells.push(`<div class="cell" style="background:var(--l${lvl})" title="${tip}"></div>`);
    }
  }
  $('#months').innerHTML = months.join('');
  $('#heat').innerHTML = cells.join('');
  $('#heatmetric').textContent = m.label;
}

function renderHours(rows) {
  const m = METRICS[state.metric];
  const buckets = Array.from({length:24}, blank);
  // Hour is not on the row grain, so this reflects the day-level mix only when
  // a single day is selected. Hidden otherwise rather than shown as a lie.
  const hourly = DATA.hours || null;
  const host = $('#hourscard');
  if (!hourly) { host.style.display = 'none'; return; }
  host.style.display = '';
  let peak = 0;
  for (const h of hourly) peak = Math.max(peak, h[state.metric === 'cost' ? 1 : 2]);
  $('#hours').innerHTML = hourly.map(h => {
    const v = h[state.metric === 'cost' ? 1 : 2];
    const lvl = v <= 0 ? 0 : Math.max(1, Math.min(5, Math.ceil(Math.pow(v/peak, 0.25) * 5)));
    return `<i style="background:var(--l${lvl})" title="${String(h[0]).padStart(2,'0')}:00  ${m.fmt(v)}"></i>`;
  }).join('');
}

function table(hostId, title, map) {
  const host = $('#' + hostId);
  if (!map.size) { host.innerHTML = `<div class="empty">nothing matches these filters</div>`; return; }
  const key = state.sort[hostId] || {col: 'cost', dir: 'desc'};
  const cols = [['name', title], ['input','input'], ['cw','cache write'], ['cr','cache read'],
                ['out','output'], ['n','responses'], ['cost','est. cost']];
  const rows = [...map.entries()].map(([name, b]) =>
    ({name, input:b.input, cw:b.c5+b.c1, cr:b.cr, out:b.out, n:b.n, cost:b.cost}));
  rows.sort((a, b) => {
    const x = a[key.col], y = b[key.col];
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return key.dir === 'desc' ? -c : c;
  });
  const head = cols.map(([c, l]) =>
    `<th data-col="${c}"${key.col===c?` aria-sort="${key.dir==='desc'?'descending':'ascending'}"`:''}>${l}</th>`).join('');
  const body = rows.map(r => `<tr><td>${r.name}</td><td>${fmt(r.input)}</td><td>${fmt(r.cw)}</td>`
    + `<td>${fmt(r.cr)}</td><td>${fmt(r.out)}</td><td>${r.n.toLocaleString()}</td>`
    + `<td>${usd(r.cost)}</td></tr>`).join('');
  host.innerHTML = `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  host.querySelectorAll('th').forEach(th => th.onclick = () => {
    const col = th.dataset.col;
    const cur = state.sort[hostId] || {col:'cost', dir:'desc'};
    state.sort[hostId] = {col, dir: cur.col === col && cur.dir === 'desc' ? 'asc' : 'desc'};
    render();
  });
}

function renderQuota() {
  const host = $('#quotacard');
  if (!DATA.quota) {
    host.innerHTML = `<h2>Plan utilization</h2><div class="empty">not signed in, or the endpoint was unreachable</div>`;
    return;
  }
  const rows = DATA.quota.windows.map(w => {
    const pct = Math.max(0, Math.min(100, w.utilization));
    const cls = pct < 75 ? 'good' : (pct < 90 ? 'warn' : 'hot');
    let left = '';
    if (w.remaining != null) { const h = Math.floor(w.remaining/3600);
      left = 'resets in ' + h + 'h ' + String(Math.floor((w.remaining%3600)/60)).padStart(2,'0') + 'm'; }
    return `<div class="meter"><div>${w.label}</div><div class="track">`
      + `<div class="fill ${cls}" style="width:${pct.toFixed(1)}%"></div></div>`
      + `<div class="pct">${pct.toFixed(0)}%</div><div class="reset-in">${left}</div></div>`;
  }).join('');
  host.innerHTML = `<h2>Plan utilization <span class="tag">account-wide</span>`
    + `<span class="tag">filters do not apply</span></h2>${rows}`
    + `<p class="note">This is the only figure here that counts Claude Code on the web, `
    + `Cowork remote sessions and claude.ai chat. Those run in throwaway cloud containers `
    + `and never write local logs, so everything else on this page is local-only. `
    + `Read via ${DATA.quota.source}.</p>`;
}

function renderBlock() {
  const host = $('#blockcard');
  if (!DATA.block) { host.style.display = 'none'; return; }
  host.style.display = '';
  const b = DATA.block;
  const t = new Date(b.start), e = new Date(b.end);
  const hhmm = d => String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
  host.innerHTML = `<h2>Current rate-limit window <span class="tag">live</span></h2>`
    + `<div class="kpis" style="margin:0">`
    + `<div class="kpi"><dt>window</dt><dd>${hhmm(t)}&ndash;${hhmm(e)}</dd></div>`
    + `<div class="kpi"><dt>spent so far</dt><dd>${usd(b.cost)}</dd></div>`
    + `<div class="kpi"><dt>burn rate</dt><dd>${fmt(b.rate)}<small>tokens / min</small></dd></div>`
    + `<div class="kpi"><dt>projected at close</dt><dd>${usd(b.projected_cost)}</dd></div></div>`;
}

function render() {
  const rows = filtered();
  const days = byDay(rows);
  renderKpis(rows, days);
  renderHeat(days);
  renderHours(rows);
  table('tmodel', 'model', groupBy(rows, I.MODEL, DATA.models));
  table('tproject', 'project', groupBy(rows, I.PROJ, DATA.projects));
  table('tsource', 'source', groupBy(rows, I.SRC, DATA.sources));
  $('#count').textContent = rows.length.toLocaleString() + ' row(s) in view';
}

function setupFilters() {
  document.querySelectorAll('.chips button[data-range]').forEach(b => b.onclick = () => {
    state.range = b.dataset.range;
    document.querySelectorAll('.chips button[data-range]').forEach(x =>
      x.setAttribute('aria-pressed', String(x === b)));
    render();
  });
  const fill = (id, values, key) => {
    const el = $(id);
    el.innerHTML = '<option value="all">all</option>'
      + values.map(v => `<option value="${v}">${v}</option>`).join('');
    el.onchange = () => { state[key] = el.value; render(); };
  };
  fill('#fsource', DATA.sources, 'source');
  fill('#fproject', [...DATA.projects].sort(), 'project');
  fill('#fmodel', [...DATA.models].sort(), 'model');
  const met = $('#fmetric');
  met.value = state.metric;
  met.onchange = () => { state.metric = met.value; render(); };
  $('#freset').onclick = () => {
    state.range = 'all'; state.source = 'all'; state.project = 'all'; state.model = 'all';
    $('#fsource').value = 'all'; $('#fproject').value = 'all'; $('#fmodel').value = 'all';
    document.querySelectorAll('.chips button[data-range]').forEach(x =>
      x.setAttribute('aria-pressed', String(x.dataset.range === 'all')));
    render();
  };
}

setupFilters();
renderQuota();
renderBlock();
render();
"""


def build_page(dataset: dict) -> str:
    css = _CSS
    for i in range(6):
        css = css.replace(f"__L{i}__", LIGHT[i]).replace(f"__D{i}__", DARK[i])

    notes = []
    if dataset["duplicates"]:
        notes.append(f"{dataset['duplicates']:,} repeated content-block rows collapsed into their response.")
    if dataset["subagent"]["responses"]:
        notes.append(
            f"Subagents account for {dataset['subagent']['responses']:,} of those responses "
            f"({dataset['subagent']['tokens']:,} tokens). They are included above; "
            "run with --no-sidechains to exclude them.")
    if dataset["restored"]:
        notes.append(f"{len(dataset['restored'])} row(s) restored from stored history, "
                     "which have no project or source attribution.")
    if dataset["unknown_models"]:
        notes.append("Counted in tokens but not priced: " + ", ".join(dataset["unknown_models"]) + ".")
    notes.append("Cost is what these tokens would have cost at list API rates, not what you were billed.")

    payload = json.dumps(dataset, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Claude usage</title><style>{css}</style></head><body>
<div class="wrap">
  <h1>Claude usage</h1>
  <p class="sub">Local logs from Claude Code and Cowork, plus account-wide plan utilization.
     Generated {dataset['generated'].replace('T', ' ')} &middot; timezone {dataset['timezone']}
     &middot; <span id="count"></span></p>

  <div class="filters">
    <div class="chips" role="group" aria-label="date range">
      <button data-range="7">7d</button>
      <button data-range="30">30d</button>
      <button data-range="90">90d</button>
      <button data-range="365">1y</button>
      <button data-range="all" aria-pressed="true">all</button>
    </div>
    <label class="f">source <select id="fsource"></select></label>
    <label class="f">project <select id="fproject"></select></label>
    <label class="f">model <select id="fmodel"></select></label>
    <label class="f">metric <select id="fmetric">
      <option value="cost">cost</option>
      <option value="total">total tokens</option>
      <option value="output">output tokens</option>
      <option value="responses">responses</option>
    </select></label>
    <button class="reset" id="freset">reset</button>
  </div>

  <dl class="kpis" id="kpis"></dl>
  <div class="card" id="quotacard"></div>
  <div class="card" id="blockcard"></div>

  <div class="card">
    <h2>Daily activity <span class="tag" id="heatmetric">cost</span></h2>
    <div class="scroll"><div class="months" id="months"></div><div class="grid" id="heat"></div></div>
    <div class="legend"><span>Less</span>
      <i style="background:var(--l0)"></i><i style="background:var(--l1)"></i>
      <i style="background:var(--l2)"></i><i style="background:var(--l3)"></i>
      <i style="background:var(--l4)"></i><i style="background:var(--l5)"></i>
      <span>More</span></div>
  </div>

  <div class="card" id="hourscard" style="display:none">
    <h2>Time of day <span class="tag">all data, filters do not apply</span></h2>
    <div class="hours" id="hours"></div>
  </div>

  <div class="card"><h2>By model</h2><div id="tmodel"></div></div>
  <div class="grids">
    <div class="card"><h2>By project</h2><div id="tproject"></div></div>
    <div class="card"><h2>By source</h2><div id="tsource"></div></div>
  </div>

  <p class="note">{" ".join(notes)}</p>
</div>
<script>const DATA = {payload};</script>
<script>{_js()}</script>
<script>
for (const el of document.querySelectorAll('.scroll')) el.scrollLeft = el.scrollWidth;
</script>
</body></html>"""


def write(dataset: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_page(dataset))
