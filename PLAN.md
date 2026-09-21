# ccburn — implementation plan

A complete usage, cost and analysis dashboard for Claude Code, Claude Code on the
web, and Cowork. This document is the spec. It names the files, the interfaces,
what is out of scope, and how each phase proves it works.

---

## 0. How to use this plan with Claude Code

Work **one phase at a time**, in order. Phases are sized to fit a single session
without exhausting context.

For each phase:

1. Start a fresh session and `/clear`. Do not carry a finished phase's context forward.
2. Enter plan mode (`Shift+Tab`). Prompt:
   `Read PLAN.md phase N and the files it names. Do not write code yet. Tell me what you will change and what could break.`
3. Approve, implement, then run that phase's **Gate** command.
4. Adversarial review before calling it done:
   `Use a subagent to review the diff against PLAN.md phase N. Check every requirement is implemented, the listed edge cases have tests, and nothing outside scope changed. Report gaps that affect correctness, not style.`
5. Commit. One phase, one PR.

**Rules that apply to every phase**

- The Gate is not optional. A phase is done when its Gate passes and the reviewer finds no correctness gap.
- If you correct Claude twice on the same point, `/clear` and restart the phase with a sharper prompt.
- Use subagents for codebase investigation so exploration does not fill the main context.
- Never widen a phase. Spotted something out of scope? Add it to "Deferred" at the bottom of this file.

---

## 1. Where we are

Working and verified against a real account:

| Piece | File | State |
|---|---|---|
| Local log parsing, Claude Code + Cowork | `ccburn/sources.py` | done |
| Pricing incl. per-TTL cache, fast mode, geo | `ccburn/pricing.py` | done |
| Aggregation, 5h blocks, burn rate | `ccburn/aggregate.py` | done |
| Account-wide plan utilization | `ccburn/quota.py` | done |
| Cloud session harvest | `ccburn/store.py` `ccburn/cli.py` | done |
| SQLite store | `ccburn/store.py` | done |
| Durable history across log pruning | `ccburn/history.py` | done |
| Diagnostics | `ccburn/doctor.py` | done |
| Terminal report | `ccburn/terminal.py` | done |
| Single-file interactive HTML | `ccburn/ui.py` | done, to be superseded by the React app |
| Dashboard server | `ccburn/dashboard.py` | done, to be replaced by FastAPI |

Numbers proven on real data: content-block dedup avoids a **2.8x** overcount;
per-TTL cache pricing moves the cache-write line **38%**; five cloud sessions
carried **$120 of web usage** absent from every local log.

---

## 2. Data sources — the part that took the longest to learn

Do not re-derive this. It is the core asset of the project.

### 2.1 What each surface leaves behind

| Surface | Per-response tokens | Per-session totals | Counted in plan utilization |
|---|---|---|---|
| Claude Code local | `~/.claude/projects/**/*.jsonl` | derived | yes |
| Cowork local | `<desktop dir>/{local-agent-mode-sessions,claude-code-sessions}/**/audit.jsonl` | derived | yes |
| Claude Code web | **none** | **session API** | yes |
| Cowork remote | **none** | **session API** | yes |
| claude.ai chat | none | none | yes |

Web and remote sessions run in containers destroyed with the session. Their logs
never reach local disk. Their **totals** are still reachable through the session
API — this is the single most important finding in the project.

### 2.2 Local JSONL shape

Assistant records carry:

```jsonc
{
  "type": "assistant",
  "timestamp": "2026-09-15T01:00:36.274Z",
  "requestId": "req_011EXAMPLEREQUEST000000",
  "apiBlockIndex": 0,          // repeats per content block — dedup on this
  "isSidechain": false,        // true = subagent turn
  "cwd": "/home/user/project", // project attribution
  "sessionId": "...",
  "message": {
    "id": "msg_011EXAMPLEMESSAGE000000",
    "model": "claude-opus-5",
    "usage": {
      "input_tokens": 2,
      "cache_creation_input_tokens": 75732,
      "cache_creation": {
        "ephemeral_5m_input_tokens": 0,     // bills at 1.25x input
        "ephemeral_1h_input_tokens": 75732  // bills at 2x input
      },
      "cache_read_input_tokens": 0,
      "output_tokens": 763,
      "output_tokens_details": { "thinking_tokens": 529 },
      "server_tool_use": { "web_search_requests": 0 },  // $10/1000
      "speed": "standard",        // "fast" = premium pricing
      "inference_geo": "global"   // "us" = 1.1x everything
    }
  }
}
```

Other record types worth reading:

- `last-prompt` → `{lastPrompt, sessionId, leafUuid}`. **This is the "what you worked on" title.** No API call, no OAuth, no model.
- `user` → first message is the fallback title.
- `summary` → appears after compaction, better title when present.

Cowork uses the same `message.usage` payload with a renamed envelope:
`session_id`, `_audit_timestamp`, `_audit_hmac`, `client_platform: "desktop_app"`.

### 2.3 Cloud session API

Returns per session:

```jsonc
{
  "id": "session_01H1...",
  "title": "Claude usage heatmap research",   // already human-readable
  "created_at": "...", "updated_at": "...",
  "origin": "web_claude_ai",                  // | claude_code_cli | claude_code_vscode
  "environment_kind": "anthropic_cloud",      // | bridge
  "tags": ["cowork-remote"],                  // cowork-* overrides surface
  "session_context": {
    "model": "claude-opus-5",
    "sources": [{"git_repository": {"url": "https://github.com/owner/repo"}}]
  },
  "external_metadata": {
    "usage": {"input_tokens": 177322, "output_tokens": 134766,
              "cache_read_tokens": 31668366, "cache_write_tokens": 600999,
              "cost_usd": 25.362093},
    "context_usage": {"used_tokens": 451019, "max_tokens": 1000000},
    "rate_limit_info": {"rateLimitType": "seven_day",
                        "status": "allowed_warning", "resetsAt": 1789790400}
  }
}
```

Three features fall straight out of this: **session usage**, **context-window
nudges** (`context_usage`), and **burn/spike alerts** (`rate_limit_info.status`).

**Caveat:** `cache_write_tokens` is a single figure with no TTL split, so harvested
cloud cost uses the API's own `cost_usd` rather than recomputing. Never mix the two.

Access, in preference order:
1. `list_sessions` via the `claude-code-remote` MCP tool (works today; `tags` filter needs an OAuth caller).
2. `ccburn harvest --file sessions.json` ingesting a saved listing (works today, implemented).
3. A direct authenticated endpoint — **not yet discovered**. `/api/oauth/usage` is the account-level one and is already used by `ccburn/quota.py`; the per-session listing endpoint is undocumented. Phase 2 spikes this.

### 2.4 Retention

Claude Code deletes transcripts older than `cleanupPeriodDays` (default **30**) at
**every startup**. `0` disables transcript writing entirely — it is a trap, not a
fix. Recommend 3650. `ccburn/history.py` and the SQLite store preserve rollups
from first run forward but cannot recover what was already deleted.

---

## 3. Target architecture

```
ccburn/                     Python package
  ingest/                   parsers: claude_code.py, cowork.py, cloud.py, (codex.py, gemini.py later)
  pricing.py                rates, the only place they live
  store.py                  SQLite schema + queries
  analysis/                 anomaly.py, cache_scan.py, waste.py, tips.py, wrapped.py
  api/                      FastAPI app: routes, schemas, dependencies
  cli.py                    typer CLI
  static/                   built frontend, served by the API
web/                        Vite + React + TypeScript
  src/
    routes/                 Overview, Sessions, Projects, Models, Analysis, Wrapped, Settings
    components/             ui primitives, charts
    lib/                    api client, formatting, filter state
tests/                      pytest, fixtures never touch ~/.claude
docs/                       DATA-SOURCES.md, DESIGN.md
```

**Stack and why**

| Layer | Choice | Reason |
|---|---|---|
| Store | SQLite (stdlib `sqlite3`) | Zero install, one file, fast enough for years of turns. No server. |
| API | FastAPI + uvicorn | Typed request/response, automatic OpenAPI the frontend generates from. |
| CLI | Typer | Same codebase, better help than argparse. |
| Frontend | Vite + React + TypeScript | The feature list is a component tree: tabs, modals, accordions, pagination. |
| Charts | Recharts | Composable, themeable, sane defaults. |
| Styling | CSS custom properties + CSS modules | No utility-class dependency; theming is one token swap. |
| Tables | TanStack Table | Sorting, pagination, column visibility, without writing it. |
| State | TanStack Query + URL search params | Filters belong in the URL so views are shareable and the back button works. |

Packaging stays `pip install ccburn`. The built frontend ships inside the wheel as
`ccburn/static`, so `ccburn serve` needs no Node at runtime.

---

## 4. Data model

Extends the existing `ccburn/store.py` schema. Migrations live in
`ccburn/store.py` keyed on `meta.schema`; bump `SCHEMA_VERSION` and write a
forward migration. Never drop a user's table.

```sql
turns(msg_id, request_id, ts, day, source, project, session, model,
      input, cache_5m, cache_1h, cache_read, output, thinking, web_search,
      sidechain, fast, geo, cost, PRIMARY KEY(msg_id, request_id))

sessions(id, title, origin, surface, project, model, started, ended,
         input, cache_write, cache_read, output, cost,
         context_used, context_max, harvested, raw)

prompts(session, ts, text, PRIMARY KEY(session, text))

-- new in phase 1
files(path, mtime, size, parsed_at, turn_count)   -- skip unchanged files on re-sync
quota_samples(ts, label, utilization, resets_at)  -- history for burn/spike detection
findings(id, kind, severity, day, subject, detail_json, created_at, dismissed)
```

`files` is what makes re-sync cheap: skip any file whose `(mtime, size)` is
unchanged. `quota_samples` is what makes alerts possible: a single utilization
reading cannot show a spike, a series can.

---

## 5. API surface

All under `/api`. Every response is a Pydantic model so the frontend generates
its client from OpenAPI.

```
GET  /api/summary?from&to&source&project&model&metric
GET  /api/daily?...           day -> tokens, cost, responses
GET  /api/hourly?...          hour-of-day matrix
GET  /api/sessions?...&cursor&limit     paginated, includes titles
GET  /api/sessions/{id}       one session with its turns
GET  /api/breakdown/{dim}?... dim in model|project|source|surface
GET  /api/blocks              rate-limit windows, active first
GET  /api/quota               account-wide utilization + recent samples
GET  /api/findings            analysis output, filterable by kind/severity
GET  /api/wrapped?year        year-in-review payload
GET  /api/health              store stats, last sync, last harvest, coverage gaps
POST /api/sync                re-parse local logs (streams progress)
POST /api/harvest             ingest a session listing
GET  /api/export?format=csv|json
```

Filters are shared query params parsed by one dependency in
`ccburn/api/dependencies.py`, so every endpoint filters identically.

---

## 6. Frontend

### Navigation

```
Header:  logo · Overview · Sessions · Projects · Analysis · Wrapped · [search] · [sync] · [theme]
Hero:    headline number for the current filter + plan-vs-API multiple + quota meters
Filters: sticky bar under the header — range, surface, project, model, metric
Footer:  data freshness, coverage note, version, links
```

- **Breadcrumbs** on drill-down: `Projects / DemoLedger / session "fix build"`.
- **Search** (`/` to focus) over session titles, project names and model names. Client-side over a fetched index below ~5k sessions; server-side beyond.
- **Sessions** uses infinite scroll with a cursor. Tables elsewhere use pagination.
- **Modals** for session detail and finding detail. Route-backed (`/sessions/:id`) so they deep-link and the back button closes them.
- **Accordions** on the Analysis page, one per finding kind, collapsed by default.
- **Empty, loading and error states are required for every view.** A view with only a happy path is not done.

### Design system

Read `docs/DESIGN.md` before writing any chart. It carries the validated palette.

- Sequential blue ramp for magnitude. Never a rainbow.
- Categorical hues assigned in fixed order, never cycled. Cap at 8, then "Other".
- Light and dark both explicitly defined on `:root`, plus a `[data-theme]` override that wins both ways.
- One y-axis per chart. Never dual-axis.
- Every chart has a hover tooltip and a table equivalent.
- `min-width: 0` on grid children, or wide tables push the page sideways.

---

## 7. Feature specs

Each names its data source and how to prove it works.

### 7.1 Session-grouped report
**Source:** `turns` grouped by session, unioned with harvested `sessions`.
**Shows:** title, surface, project, model mix, duration, tokens, cost, context peak.
**Verify:** a known session's total equals the sum of its turns; a harvested session appears with its API cost unchanged.

### 7.2 "What you worked on" titles
**Source:** `last-prompt` records; fall back to first user message; prefer `summary` when present; cloud sessions use the API `title`.
**Out of scope:** calling a model to summarize. The text is already on disk.
**Verify:** a fixture session with a `last-prompt` record yields that text; one without falls back to the first user message.

### 7.3 Wrapped / Year in Review
**Source:** `turns` + `sessions` for the year.
**Shows:** total spend and API-equivalent multiple, busiest day and hour, longest streak, top projects, model mix over time, biggest single session, cache savings, a shareable card.
**Verify:** snapshot test on a fixture year; totals match `/api/summary` for the same range.

### 7.4 Anomaly detection
**Definition:** a day whose cost exceeds `median(trailing 28 days) + 3 × MAD`. Median and MAD, not mean and stdev — one runaway session would poison a mean.
**Needs:** at least 14 days of history; below that, report "not enough history" rather than a finding.
**Verify:** a fixture with 30 flat days plus one 10x day yields exactly one finding; 30 flat days yield none.

### 7.5 Burn / spike alerts
**Source:** `quota_samples` deltas plus the active block's burn rate.
**Definition:** fires when projected utilization at window close exceeds 100%, or when a single response exceeds 5x the trailing-50 median.
**Verify:** synthetic sample series crossing the threshold fires exactly once, not once per sample.

### 7.6 Prompt-cache opportunity scanner
**Source:** `turns` per session, looking at `cache_read / (cache_read + input + cache_write)`.
**Definition:** flag sessions whose hit rate is below 50% while total input exceeds 200k tokens, with the dollar value of the miss.
**Out of scope:** reading prompt text to find repeated prefixes. Ratios only, no content.
**Verify:** a fixture session with 0% hit rate and 1M input tokens produces a finding whose stated saving equals `input × (rate.input − rate.cache_read)`.

### 7.7 Cost optimisation tips
**Source:** the finding set, rendered as ranked advice.
**Rules:** model mix (Opus where Sonnet would do, measured by output length), cache hit rate, subagent overhead, fast-mode spend.
**Verify:** each rule has a fixture that triggers it and one that does not.

### 7.8 AI-written weekly summary
**Source:** aggregate rollups only, sent to the Claude API. **Never send prompt text or project names unless the user opts in.**
**Config:** `ai_summary: {enabled: false, model: "claude-haiku-4-5", include_project_names: false}`. Off by default.
**Verify:** with `enabled: false` no network call is made (assert with a mocked transport); the payload contains no prompt text.

### 7.9 Token-waste health check
**Source:** `turns` plus tool-use records.
**Rules:** same file read repeatedly in one session, sessions with a high cache-read to output ratio, subagent-heavy sessions, very long sessions with low output.
**Verify:** one fixture per rule.

### 7.10 Peak-window awareness
**Source:** `quota_samples` grouped by weekday and hour.
**Shows:** when this account historically hits limits, so work can be scheduled around it.
**Verify:** fixture samples concentrated in one weekday window surface that window.

### 7.11 Context-window nudges
**Source:** `sessions.context_used / context_max` from harvest, and the live block for local sessions.
**Definition:** warn above 70%, urgent above 85%.
**Verify:** a harvested session at 451019/1000000 reports 45% and no warning; one at 900k warns.

### 7.12 Service status
**Source:** the public Anthropic status page JSON.
**Rules:** cached 5 minutes, times out in 3 seconds, never blocks a render, hidden entirely when the fetch fails.
**Verify:** with the network stubbed to fail, the dashboard still renders and the panel is absent.

### 7.13 Progress concierge / token saver
**Scope:** generate a resume brief for a project — last session title, uncommitted changes, unfinished todos — as **text the user copies**. ccburn does not talk to a running Claude session.
**Verify:** produces a brief for a fixture project; writes nothing outside its own data dir.

### 7.14 Other agent CLIs
**Deferred by the user.** Design `ccburn/ingest/` so a new provider is one module exposing `discover()` and `parse()`. Do not implement Codex or Gemini now, but do not hard-code "claude" into the schema either — `turns.source` is already free text.

---

## 8. Phases

Each phase is one session, one PR, one Gate.

### Phase 1 — Store, ingest split, incremental sync
**Do:** move parsing into `ccburn/ingest/{claude_code,cowork,cloud}.py` behind a common `discover()/parse()` interface. Add the `files` table and skip unchanged files. Add migrations keyed on `meta.schema`. Backfill `quota_samples` on every quota read.
**Out of scope:** any UI change.
**Gate:** `uv run pytest -q tests/test_ingest.py tests/test_store.py` passes, and `time ccburn sync` on an unchanged tree is under 1 second with `turn_count` unchanged.

### Phase 2 — FastAPI backend
**Do:** implement every endpoint in section 5 with Pydantic models and the shared filter dependency. Keep the CLI working against the same functions. Spike the direct session endpoint; if not found in one session's effort, keep `--file` harvest and record the finding in `docs/DATA-SOURCES.md`.
**Out of scope:** the frontend.
**Gate:** `uv run pytest -q tests/test_api.py` passes; `curl localhost:8777/api/summary` returns totals equal to `ccburn json`'s totals for the same filters.

### Phase 3 — Frontend shell
**Do:** Vite + React + TS scaffold in `web/`. Header, nav, hero, sticky filter bar, footer, theme toggle, routing, API client generated from OpenAPI, TanStack Query, filters in URL params. Overview page only, wired to real data. Loading, empty and error states.
**Out of scope:** analysis pages, Wrapped.
**Gate:** `cd web && npm run build && npm run test` passes; Playwright check asserts zero horizontal overflow at 1440/1024/768/390px and that reloading a filtered URL restores the same view.

### Phase 4 — Core views
**Status:** implemented and verified on 2026-09-19. Independent review found no
remaining correctness gaps. See `docs/PHASE-4.md` for validation and replacements.
**Do:** Sessions (infinite scroll, search, detail modal at `/sessions/:id`), Projects (drill-down + breadcrumbs), Models, heatmap with metric switch, hour-of-day, blocks.
**Gate:** Playwright drives filter changes and asserts the KPI row recomputes; session detail deep-links and the back button closes the modal.

### Phase 5 — Analysis engine
**Status:** implemented and verified on 2026-09-21. Phase Gate: 40 passing
tests. Independent review found no remaining correctness gaps.
**Implementation notes:** see `docs/PHASE-5.md` for rule thresholds, metadata
backfill and the approved local-context limitation.
**Do:** `ccburn/analysis/` implementing 7.4, 7.6, 7.7, 7.9, 7.10, 7.11. Write to `findings`. Analysis page with accordions per kind, severity badges, dismiss.
**Out of scope:** the AI summary and service status.
**Gate:** `uv run pytest -q tests/test_analysis.py` — every rule has a triggering and a non-triggering fixture.

### Phase 6 — Wrapped, alerts, AI summary, status
**Do:** 7.3, 7.5, 7.8, 7.12, 7.13.
**Gate:** Wrapped snapshot test; alert fires once not per sample; AI summary makes no network call when disabled; dashboard renders with the status fetch stubbed to fail.

### Phase 7 — Packaging and polish
**Do:** ship `web/dist` into the wheel as `ccburn/static`. `ccburn serve` opens the React app. Windows path tests. Docs. Screenshots in the README.
**Gate:** `pip install dist/*.whl` in a clean venv on Windows and Linux, then `ccburn serve` renders the dashboard with no Node present.

---

## 9. Non-goals

Stated so they do not creep in:

- A tray, menu-bar or desktop app. It is a web app. `bozdemir/claude-usage-widget` already does the tray well.
- Reading or storing prompt text beyond the title already on disk.
- Copying code from the reference tools. One is AGPL; borrowing an idea is fine, borrowing source is not.
- Multi-user, auth, or hosting. Single user, localhost.
- Recovering logs already deleted by retention. Impossible; say so.

---

## 10. Traps

Every one of these produced a wrong number or a broken page during earlier work.

1. Summing JSONL lines instead of deduping responses → 2-3x overcount.
2. One flat cache-write multiplier → 38% error on that line.
3. `date.today()` instead of the report timezone → wrong streaks and MTD.
4. Pricing an unknown model at zero → silent undercount. ccusage 20.0.20 does this for current models.
5. Grid children without `min-width: 0` → horizontal page overflow.
6. `cleanupPeriodDays: 0` → no transcripts at all.
7. Recomputing cost for harvested cloud sessions → wrong, no TTL split available. Use the API's `cost_usd`.
8. Treating a single quota reading as a trend → spurious alerts. Needs `quota_samples`.
9. Labelling local-only stats as complete → the "streak is 0 but I use Claude daily" bug.

---

## 11. Deferred

Append here rather than widening a phase.

- Codex, Gemini and other agent CLI ingestion (user will implement later).
- PNG export of the dashboard.
- Webhooks and desktop notifications.
- Localization.
- Stored `turns.cost` is computed at sync time. Incremental sync skips unchanged
  files, so a pricing-table or `pricing_overrides` change does not reprice old
  rows. Needs a `ccburn sync --full` or a reprice pass keyed on the pricing version.
- Resolved in Phase 5: `findings` (listed under "new in phase 1" in section 4)
  is created by the schema v4 migration. This replaces the earlier deferral
  because the analysis engine now persists findings.
- Pre-existing lint debt outside the Phase 1 diff: 23 `ruff check` errors
  (cli.py, doctor.py, dashboard.py, quota.py, sources.py), and `store.py` was
  already unformatted before Phase 1.
- Bug, predates Phase 1 (reproduced on 2ac5456): `ccburn sessions` with stdout
  redirected on Windows exits 1 with `UnicodeEncodeError: 'charmap' codec` when a
  prompt title has characters outside cp1252. Needs a failing test, then
  `sys.stdout.reconfigure(errors="replace")` or UTF-8 output in the CLI.
- Predates Phase 1: `resolve_tz("local")` in `cli.py` returns today's fixed
  offset and applies it to all history, so in DST zones last season's turns near
  midnight land on the neighbouring day. Resolve local to an IANA zone instead.
- `cli.build_report()` always calls `quota.read(cfg)`, even for API endpoints
  that never use the quota result (`/api/daily`, `/api/breakdown/*`, etc.).
  Pre-existing behavior from the CLI, not a phase 2 regression, but a frontend
  hitting several of these per filter change will trigger the account-wide
  quota fetch repeatedly. Worth splitting quota out of `build_report` or
  caching it once the frontend exists to actually feel this.
- Phase 2 removed `ccburn/dashboard.py` (the stdlib `http.server` dashboard).
  Reason: it duplicated the report pipeline FastAPI now serves under `/`,
  `/api/dataset` and `/api/usage` from `ccburn/api/app.py`, and PLAN.md already
  marked it "to be replaced by FastAPI." Its `--refresh` live-reload script (a
  `setInterval` re-fetch-and-patch) was not ported — no equivalent exists on
  the FastAPI routes today. Phase 3's React app will need its own polling or
  SSE for live refresh; it should not resurrect the old script.
- Phase 3 hero omits the plan-vs-API multiple: the API does not expose
  `plan_usd_per_month`. Add it to `SummaryOut` (or `/api/health`) and render it.
- Phase 3 omits header search (`/` to focus) and date-range presets. Presets
  must take "today" from the server's report timezone, not the browser clock
  (trap 3), so they need a `today` field from the API first.
- Phase 3 omits live refresh (polling or SSE); see the Phase 2 note above.
- Trap 3 still lives in pre-existing code: `date.today()` in
  `aggregate.py:161,177`, `terminal.py:62`, `ui.py:77`, `doctor.py:45`. Report
  a failing test, then pass the report timezone's date. Ruff now reports 21
  pre-existing errors in total.
- `ccburn serve` still serves the legacy HTML page at `/`. Serving the React
  build from `ccburn/static` with an SPA fallback is Phase 7.
- Filtered reports skip the history rollup (fixed in Phase 3, see
  `build_report`), so a filtered view cannot include days whose logs retention
  deleted. The UI says so. Storing history per source/project/model would lift
  this.
- Phase 4's Recharts/TanStack Table build exceeds Vite's 500 kB chunk warning.
  Split route/chart bundles during Phase 7 packaging and polish. npm also reports
  7 audit findings; dependency upgrades need a separate compatibility check.
- Phase 5: local context percentages need a trustworthy per-session context
  snapshot and maximum. Rate-limit blocks cannot supply that denominator;
  local context is explicitly unavailable while harvested nudges are supported.
