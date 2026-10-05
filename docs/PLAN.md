# vibewatt: implementation plan

A complete usage, cost and analysis dashboard for Claude Code, Claude Code on the
web, and Cowork. This document is the spec. It names the files, the interfaces,
what is out of scope, and how each phase proves it works.

---

## 0. How to use this plan

Work **one phase at a time**, in order. Phases are sized to fit a single session
without exhausting context.

For each phase:

1. Start a fresh session and `/clear`. Do not carry a finished phase's context forward.
2. Enter plan mode (`Shift+Tab`). Prompt:
   `Read docs/PLAN.md phase N and the files it names. Do not write code yet. Tell me what you will change and what could break.`
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
| Local log parsing, Claude Code + Cowork | `vibewatt/sources.py` | done |
| Pricing incl. per-TTL cache, fast mode, geo | `vibewatt/pricing.py` | done |
| Aggregation, 5h blocks, burn rate | `vibewatt/aggregate.py` | done |
| Account-wide plan utilization | `vibewatt/quota.py` | done |
| Cloud session harvest | `vibewatt/store.py` `vibewatt/cli.py` | done |
| SQLite store | `vibewatt/store.py` | done |
| Durable history across log pruning | `vibewatt/store.py` | retained turns and rollups |
| Diagnostics | `vibewatt/doctor.py` | done |
| Terminal report | `vibewatt/terminal.py` | done |
| React dashboard | `web/src/`, `vibewatt/static/` | built assets packaged in the wheel |
| Dashboard server | `vibewatt/api/app.py` | FastAPI with local-only middleware |

**Audit 2026-09-22:** phases 1-6 were audited against this plan. 128 findings,
evidence in the 2026-09-22 audit report (removed in Phase 11), one line each in section 11, scheduled
as Phase 6.5a-g below. Phase 7 refreshes the table above to reflect the store-only reporting pipeline.

Numbers proven on real data: content-block dedup avoids a **2.8x** overcount of
input/output (2.1x across all token types, A-101);
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
  "apiBlockIndex": 0,          // one line per content block; usage repeats
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
`session_id`, `request_id`, `_audit_timestamp`, `_audit_hmac`,
`client_platform: "desktop_app"`.

**Dedup rule (amended 2026-09-22, Phase 6.5b).** Every line of one response
repeats its `usage`; the first line of a streamed response is a placeholder
(`output_tokens` 1-3). Key on `(message.id, requestId)` (Cowork spells it `request_id`) and keep the
per-field maximum: the first line of a streamed response carries placeholder
`output_tokens`. Drop a sidechain line whose `message.id` is on a main-thread
line. Without a `requestId`, key on `(session, message.id, timestamp)`. The
result must not depend on file order. Code: `sources.dedupe()` and
`store.upsert_turns()`.
Measured on real data: the old first-line rule stored 5.62M output tokens, the
per-field maximum stores 6.98M (+24%). An independent re-implementation
over the same logs matches the store exactly (8,094 responses, 43 days).

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
2. `vibewatt harvest --file sessions.json` ingesting a saved listing (works today, implemented).
3. A direct authenticated endpoint — **not yet discovered**. `/api/oauth/usage` is the account-level one and is already used by `vibewatt/quota.py`; the per-session listing endpoint is undocumented. Phase 2 spikes this.

### 2.4 Retention

Claude Code deletes transcripts older than `cleanupPeriodDays` (default **30**) at
**every startup**. `0` disables transcript writing entirely — it is a trap, not a
fix. Recommend 3650. The SQLite store keeps every turn from its first sync
forward but cannot recover what was already deleted.

**Corrected 2026-09-22 (audit):** since Claude Code 2.1.89 `cleanupPeriodDays: 0`
fails settings validation instead of silently disabling writing; older versions
still have the trap. Minimum is 1, default still 30 and the sweep runs as a
background task after start. Since 2.1.248 desktop and Cowork transcripts are
exempt from age cleanup unless `desktopSessionCleanupPeriodDays` or a managed
`cleanupPeriodDays` is set. The promise above does not hold today: history.json
permanently loses partially pruned days (A-001). Phase 6.5b makes the store the
only source of headline numbers.

---

### 2.5 Plan utilization sources (amended 2026-09-22, Phase 6.5d)

Order of preference. Every reading becomes a row in `quota_samples`
`(ts, key, label, scope, utilization, resets_at, source)`. Readers take the
newest sample per window from the store.

1. **`vibewatt statusline`**, set as Claude Code's `statusLine` command. Claude
   Code pipes documented JSON on stdin at every refresh:
   `rate_limits.{five_hour,seven_day,spend_limit}.{used_percentage,resets_at}`,
   `resets_at` in Unix seconds. The block is present only for Pro/Max (or behind a
   spend-limited gateway), only after the session's first response. A window
   is dropped once its reset passes. Free, live, no network.
2. **Desktop `plan-usage-history.json`**, read only
   (`%APPDATA%\Claude`, `~/Library/Application Support/Claude`,
   `~/.config/Claude`). Version 2: `{version, samples: [{t (ms), org, u: {fh, sd}}]}`,
   a sample every 15 minutes in whole percents and **no reset times**. Imported
   only for known versions, deduped on `(org, t)`. With one org, account-wide
   samples and that org are the same series.
3. **`/api/oauth/usage`**, a fallback only: at most one call per 10 minutes, and
   none while a sample under 10 minutes old exists. A 429 backs off 10 minutes,
   doubling to 6 hours. Only the background sync and the CLI may call it, never a
   page request. Windows are parsed generically.

Resets are detected from the data (a new `resets_at`, a drop of more than 2
points, or a gap longer than the window), never from an assumed schedule.

## 3. Target architecture

```
vibewatt/                     Python package
  ingest/                   parsers: claude_code.py, cowork.py, cloud.py, (codex.py, gemini.py later)
  pricing.py                rates, the only place they live
  store.py                  SQLite schema + queries
  analysis/                 anomaly.py, cache_scan.py, waste.py, tips.py, wrapped.py
  api/                      FastAPI app: routes, schemas, dependencies
  cli.py                    argparse CLI
  static/                   built frontend, served by the API
web/                        Vite + React + TypeScript
  src/
    pages/                  Overview, Sessions, Breakdown, Analysis, Wrapped
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
| CLI | argparse | Standard library CLI shared with the API pipeline. |
| Frontend | Vite + React + TypeScript | The feature list is a component tree: tabs, modals, accordions, pagination. |
| Charts | Recharts | Composable, themeable, sane defaults. |
| Styling | CSS custom properties + shared stylesheet | No utility-class dependency; theming is one token swap. |
| Tables | TanStack Table | Sorting, pagination, column visibility, without writing it. |
| State | TanStack Query + URL search params | Filters belong in the URL so views are shareable and the back button works. |

Packaging stays `pip install vibewatt`. The built frontend ships inside the wheel as
`vibewatt/static`, so `vibewatt serve` needs no Node at runtime.

---

## 4. Data model

Extends the existing `vibewatt/store.py` schema. Migrations live in
`vibewatt/store.py` keyed on `meta.schema`; bump `SCHEMA_VERSION` and write a
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
GET  /api/hourly?...          hour-of-day vector (24 buckets; decided in 6.5e, A-115)
GET  /api/sessions?...&cursor&limit     paginated, includes titles
GET  /api/sessions/{id}       one session with its turns
GET  /api/breakdown/{dim}?... dim in model|project|source
GET  /api/blocks              rate-limit windows, active first
GET  /api/quota               account-wide utilization + recent samples
GET  /api/findings            analysis output, filterable by kind/severity
GET  /api/wrapped?year        year-in-review payload
GET  /api/health              store stats, last sync, last harvest, coverage gaps
POST /api/sync                re-parse local logs (streams progress)
GET  /api/session-facets      stored filter options
GET  /api/reconciliation      raw Stats rule versus deduped local totals
GET  /api/alerts              burn and spike evidence
GET  /api/status              optional public service status
GET  /api/concierge?project   read-only resume brief
GET  /api/findings/{id}       finding detail
POST /api/findings/{id}/dismiss
POST /api/analysis           refresh analysis snapshot
POST /api/weekly-summary     opt-in AI summary
POST /api/harvest             ingest a session listing
GET  /api/export?format=csv|json
```

Filters are shared query params parsed by one dependency in
`vibewatt/api/dependencies.py`, so every endpoint filters identically.

---

## 6. Frontend

### Navigation

```
Header:  logo · Overview · Sessions · Projects · Analysis · Wrapped · [search] · [sync] · [theme]
Hero:    headline number for the current filter + plan-vs-API multiple + quota meters
Filters: sticky bar under the header — range, surface, project, model, metric
Footer:  data freshness, coverage note, version, links
```

- **Breadcrumbs** on drill-down: `Projects / demo-app / session "fix build"`.
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
Implemented in Phases 4 and 6.5b, verified by store/session reconciliation tests.
**Source:** `turns` grouped by session, unioned with harvested `sessions`.
**Shows:** title, surface, project, model mix, duration, tokens, cost, context peak.
**Verify:** a known session's total equals the sum of its turns; a harvested session appears with its API cost unchanged.

### 7.2 "What you worked on" titles
Implemented and corrected in Phase 6.5c; the current title rule is recorded there.
**Source:** `last-prompt` records; fall back to first user message; prefer `summary` when present; cloud sessions use the API `title`.
**Out of scope:** calling a model to summarize. The text is already on disk.
**Verify:** a fixture session with a `last-prompt` record yields that text; one without falls back to the first user message.

### 7.3 Wrapped / Year in Review
**Source:** `turns` + `sessions` for the year.
**Shows:** total spend and API-equivalent multiple, busiest day and hour, longest streak, top projects, model mix over time, biggest single session, cache savings, a shareable card.
**Verify:** snapshot test on a fixture year; totals match `/api/summary` for the same range.

### 7.4 Anomaly detection
**Definition (amended 2026-09-22, Phase 6.5f):** a day whose cost exceeds
`median + 3 × max(1.4826 × MAD, 0.1 × median)` over the previous 28 active,
fully priced days. Median and MAD, not mean and stdev: one runaway session
would poison a mean. Inactive days are not $0 days. The floor keeps a flat
baseline (MAD = 0) from flagging noise.
**Needs:** at least 14 earlier active days; below that, say "not enough history". A
range with no activity says so instead. A day with unpriced spend names the
models.
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
**Deviation (recorded 2026-09-22, A-119):** the implemented saving also converts cache
writes to reads, so a write-heavy session states more than the formula above (8.0
vs 1.35 on 300k input with 700k 1h writes). `docs/ANALYSIS.md` describes savings as
upper-bound scenarios. The spec fixture, which has no writes, still matches.

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
**Source:** `sessions.context_used / context_max` from harvest. Local sessions
(amended 2026-09-26, Phase 8 package 2.1): the prompt size of the latest
main-thread response over the model's verified window, for sessions active in the
last 30 minutes. A rate-limit block is never a context source.
**Definition:** warn above 70%, urgent above 85%.
**Verify:** a harvested session at 451019/1000000 reports 45% and no warning; one at 900k warns.

### 7.12 Service status
**Source:** the public Anthropic status page JSON.
**Rules:** cached 5 minutes, times out in 3 seconds, never blocks a render, hidden entirely when the fetch fails.
**Verify:** with the network stubbed to fail, the dashboard still renders and the panel is absent.

### 7.13 Progress concierge / token saver
**Scope:** generate a resume brief for a project — last session title, uncommitted changes, unfinished todos — as **text the user copies**. vibewatt does not talk to a running Claude session.
**Verify:** produces a brief for a fixture project; writes nothing outside its own data dir.

### 7.14 Other agent CLIs
**Deferred by the user.** Design `vibewatt/ingest/` so a new provider is one module exposing `discover()` and `parse()`. Do not implement Codex or Gemini now, but do not hard-code "claude" into the schema either — `turns.source` is already free text.
**Scheduled 2026-09-26:** ChatGPT/Codex CLI and Gemini CLI/Antigravity ingestion
are Phase 10, after the Phase 8 backlog and the Phase 9 verification. Phase 8 must
not build them.

---

## 8. Phases

Each phase is one session, one PR, one Gate.

### Phase 1 — Store, ingest split, incremental sync
**Do:** move parsing into `vibewatt/ingest/{claude_code,cowork,cloud}.py` behind a common `discover()/parse()` interface. Add the `files` table and skip unchanged files. Add migrations keyed on `meta.schema`. Backfill `quota_samples` on every quota read.
**Out of scope:** any UI change.
**Gate:** `uv run pytest -q tests/test_ingest.py tests/test_store.py` passes, and `time vibewatt sync` on an unchanged tree is under 1 second with `turn_count` unchanged.

### Phase 2 — FastAPI backend
**Do:** implement every endpoint in section 5 with Pydantic models and the shared filter dependency. Keep the CLI working against the same functions. Spike the direct session endpoint; if not found in one session's effort, keep `--file` harvest and record the finding in `docs/DATA-SOURCES.md`.
**Out of scope:** the frontend.
**Gate:** `uv run pytest -q tests/test_api.py` passes; `curl localhost:8777/api/summary` returns totals equal to `vibewatt json`'s totals for the same filters.

### Phase 3 — Frontend shell
**Do:** Vite + React + TS scaffold in `web/`. Header, nav, hero, sticky filter bar, footer, theme toggle, routing, API client generated from OpenAPI, TanStack Query, filters in URL params. Overview page only, wired to real data. Loading, empty and error states.
**Out of scope:** analysis pages, Wrapped.
**Gate:** `cd web && npm run build && npm run test` passes; Playwright check asserts zero horizontal overflow at 1440/1024/768/390px and that reloading a filtered URL restores the same view.

### Phase 4 — Core views
**Status:** implemented and verified on 2026-09-19. Independent review found no
remaining correctness gaps. Its phase notes were removed in the 2026-09-22 cleanup (see below).
**Do:** Sessions (infinite scroll, search, detail modal at `/sessions/:id`), Projects (drill-down + breadcrumbs), Models, heatmap with metric switch, hour-of-day, blocks.
**Gate:** Playwright drives filter changes and asserts the KPI row recomputes; session detail deep-links and the back button closes the modal.

### Phase 5 — Analysis engine
**Status:** implemented and verified on 2026-09-21. Phase Gate: 40 passing
tests. Independent review found no remaining correctness gaps.
**Implementation notes:** `docs/ANALYSIS.md` (rule thresholds, repeated-read metadata
and the approved local-context limitation).
**Do:** `vibewatt/analysis/` implementing 7.4, 7.6, 7.7, 7.9, 7.10, 7.11. Write to `findings`. Analysis page with accordions per kind, severity badges, dismiss.
**Out of scope:** the AI summary and service status.
**Gate:** `uv run pytest -q tests/test_analysis.py` — every rule has a triggering and a non-triggering fixture.

### Phase 6 — Wrapped, alerts, AI summary, status
**Status:** implemented and verified on 2026-09-21. Phase Gate: 25 passing
tests plus 30 passing browser checks. Independent review found no remaining
correctness gaps. Coverage and bounds: `docs/ANALYSIS.md`.
**Do:** 7.3, 7.5, 7.8, 7.12, 7.13.
**Gate:** Wrapped snapshot test; alert fires once not per sample; AI summary makes no network call when disabled; dashboard renders with the status fetch stubbed to fail.

### Phase 6.5: Audit fixes and spec corrections (added 2026-09-22)
**Why:** the phase 1-6 audit of 2026-09-22 (report removed in Phase 11) found 128 issues:
1 critical, 6 high, 55 medium, 50 low, 16 info. The worst ones change headline
numbers. Output tokens are undercounted by 7.6-16% and pruned days lose usage
permanently. Localhost security is open. The burn alert almost never fires.
Online research also showed that parts of section 2 are out of date. This work
has to land before Phase 7 packages it.
Split into seven sessions, each one PR, run in order. Every fix starts with a
failing test that reproduces the audit item (see the A-numbers). Decisions
taken with the user on 2026-09-22 are marked *(decided)*.

#### Phase 6.5a: Rename to vibewatt *(decided)*
**Why:** the PyPI name `ccburn` belongs to an unrelated tool in the same space
(JuanjoFuchs/ccburn, 24 releases). The project is also growing beyond Claude
(Codex and Antigravity are planned), so the new name is agent-neutral.
`vibewatt` had no GitHub repositories and no PyPI or npm package on
2026-09-22.
**Do:** a mechanical rename: package dir `ccburn/` to `vibewatt/`, CLI
`vibewatt`, project and web package names and env vars `CCBURN_*` to
`VIBEWATT_*` (read the old names as a fallback for one release and print a
deprecation note). Move the data dir with a one-time copy that leaves the old
store in place. Update docs, skills and agent configs. Record the rename and
its reason in this file.
**Out of scope:** any behaviour change. Log paths that mention Claude stay
as-is (they are data locations). The GitHub repo rename is done by the user.
**Gate:** every existing gate passes under the new names. An existing
`ccburn` store and `CCBURN_DATA_DIR` are picked up and migrated (test).
`grep -ri ccburn` returns only the fallback, the migration and the changelog.
**Status (2026-09-22): done** on `chore/phase-6-5a-rename-vibewatt`. Record:
- Renamed: package `ccburn/` to `vibewatt/`, CLI and project name, web package
  `vibewatt-web`, store file `ccburn.db` to `vibewatt.db`, env vars
  `CCBURN_{DATA_DIR,CONFIG,COWORK_DIR}` to `VIBEWATT_*`, dev proxy env
  `CCBURN_API` to `VIBEWATT_API`, theme key `ccburn-theme` to `vibewatt-theme`.
- Fallbacks, removed in the release after the first `vibewatt` release:
  `config.env()` reads `CCBURN_*` with a stderr deprecation note. `config.load()`
  reads `ccburn/ccburn.json` and `./.ccburn/ccburn.json` when the new file is
  absent. The web client reads the old theme key and `CCBURN_API`.
- Migration: `config.data_dir()` copies the old default data dir once when the
  new one does not exist, and copies `ccburn.db` through the SQLite backup API
  (so WAL content is kept). `store.db_path()` does the same for a store in a
  `CCBURN_DATA_DIR`. The old files are never moved or deleted, so a downgrade
  still works. Tests: `tests/test_rename.py`.
- Kept on purpose at the time: the 2026-09-22 audit report (removed in Phase 11) was a dated record; its `ccburn/...`
  paths are evidence of the code as audited, so it was not rewritten.
- PyPI: `vibewatt` 0.0.1 (placeholder) published by the user on 2026-09-22 to
  reserve the name; ship a real release before PEP 541 treats it as abandoned.
  The GitHub repo is now `MMALI3287/vibewatt`; npm `vibewatt` is still free and
  unreserved.

#### Phase 6.5b: Dedup rule and single source of truth *(decided)*
**Do:**
- Replace the dedup non-negotiable in `CLAUDE.md`, section 2.2 and trap 1 in the
  same PR as the code. New rule:
  - Key on `(message.id, requestId)` and keep the per-field maximum, which is
    the final streamed line (A-003).
  - Drop a sidechain line whose `message.id` already appears on a main-thread
    line. `/btw` aside files replay parent messages under a new `requestId`.
  - Without a `requestId`, key on `(session, message.id, timestamp)`. LLM
    gateways reuse message ids (A-010).
  - The result must not depend on file order (A-009).
  - Cowork writes each response to both `audit.jsonl` (snake_case
    `request_id`) and a nested `.claude/projects` transcript. Count it once.
- Make the SQLite store the only source for every endpoint and CLI report.
  Sync keeps it fresh and requests never reparse logs (A-025 and the Deferred
  `build_report` quota item). Import the `history.json` rows the store lacks
  once, then retire `history.py` and record the removal (A-001, A-002, A-008,
  A-022, A-124).
- Store Claude Code `version` per turn. `files.turn_count` counts responses,
  not lines (A-114). Keep first-sync memory bounded and report progress
  (A-071).
- Cloud ingest implements the common interface, or the deviation is recorded
  (A-064).
- Tests: A-018, A-021, A-039, A-044. Correct the 2.8x claim, which holds only
  for input/output (A-101).
**Gate:** fixture suites for placeholder lines (3 then 956 gives 956), aside
replay, a gateway without `requestId`, files arriving in both orders, and
Cowork dual copies. Pruning any subset of files never lowers any day's total.
A real-data oracle run matches the store exactly. Every report endpoint
answers in < 300 ms on a 1M-turn store.
**Status (2026-09-22): done** on `fix/phase-6-5b-dedup-store`. Record:
- Dedup: `sources.dedupe()`/`merge()` and `store.upsert_turns()` (rule in 2.2).
  Schema 5 adds `turns.version`, `turns.hour`, `rollup` and `history_days`, and
  forgets file checkpoints so every file is re-read under the new rule. A
  pre-6.5b row keyed `(message.id, "")` is absorbed by the keyed row replacing it.
- Store as the only source: `aggregate.from_store()` builds every report from
  `rollup` (grain: UTC hour, local day and hour, source, project, model,
  session, sidechain). Blocks are rebuilt exactly from hourly first/last
  timestamps. `serve` syncs at startup and every `sync_interval_seconds`
  (default 60) in a background thread, caches reports per filter until the
  store's `generation` changes and warms the unfiltered report after a sync.
  Report endpoints no longer read quota (A-025); `/api/quota` does.
- **Removed `vibewatt/history.py`**, the `history` config key and the
  `--no-history` flag. Why: the store keeps every turn after its log is pruned,
  so the per-day rollup was a second, weaker copy. It lost partly pruned
  days (A-001), priced unknown models at $0 (A-002) and double counted on a
  timezone change (A-008). `store.import_history()` copies an existing
  `history.json` into `history_days` once; reports add only the per-field
  excess over the store, so it can never double count. The file is left on disk.
- `files.turn_count` counts responses (A-114). Sync reads files in batches of
  200 and reports progress (A-071).
- Deviation (A-064): `ingest.cloud` keeps a payload-shaped `parse(payload)`
  with no `discover()`. Cloud sessions have no local file, so the common
  file interface does not apply.
- Gate evidence: `tests/test_dedup_store.py` (18 tests). Real-data oracle:
  8,094 responses, 318,999 input, 6,983,384 output tokens, 0 mismatched days.
  `scripts/bench_store.py --turns 1000000`: worst first hit 237 ms
  (`/api/blocks`), unfiltered report warmed after sync in 519 ms.

#### Phase 6.5c: Parser robustness, pricing lookup, timezone and day boundary
**Do:**
- Pricing lookup by exact id after normalization. Strip date,
  `@date`, `-vN:M` and `[1m]` suffixes and all Bedrock profile prefixes. An
  unknown id is unpriced, never borrowed from a sibling (A-011, A-012). Fast
  mode on a model with no fast rate is unpriced. Validate remote rates
  (A-066). `inference_geo` values other than `us` mean 1.0x (`not_available`
  is common). Record the TTL assumed when the split is missing (A-063). Export
  carries per-row unpriced flags (A-026).
- A wrong-typed field skips that record, not the whole sync (A-023). A file
  that cannot be opened is retried, not checkpointed (A-024). Handle BOMs and
  junction loops (A-069, A-070). Count dropped records per file by reason and
  show them in doctor.
- Titles: `custom-title` > `ai-title` > `last-prompt` > first user message,
  because `summary` records no longer exist (A-059, A-060). Retain only the
  title, not every prompt (A-067). Project attribution works without `cwd`
  (A-065).
- Timezone: add `tzdata` for Windows IANA zones (A-058). The CLI heatmap uses
  the report tz (A-055). DST no longer forces a full reparse (A-113). Fix the
  Windows stdout crash (A-057).
- New config `day_start_hour` (default 0) *(decided)*. It shifts the day
  boundary for daily, streak, heatmap, Wrapped and anomaly views, so an
  8 PM-4 AM session counts as one day.
- Pricing and timezone tests that the mutation run showed to be missing:
  A-014 to A-017, A-019, A-020, A-034, A-068.
**Gate:** every surviving pricing/timezone mutant from the audit is killed by
the suite. `day_start_hour: 6` puts 20:00-04:00 JST activity on one day in
daily, streak and heatmap views.
**Status (2026-09-22): done** on `fix/phase-6-5c-parser-pricing-tz`. Record:
- Pricing: `normalize()` strips any `<region>.anthropic.` profile prefix,
  `[1m]`, `@date`, `-vN:M`, `-YYYYMMDD` and `-latest`, then `rate_for()` looks up
  the exact id: overrides, then fast rates, then built-in, then remote. An id
  nothing knows is unpriced. Fast mode without a fast rate is unpriced. Remote
  entries with a zero, negative or non-finite input/output rate are dropped.
  Every model in the owner's real logs still prices.
- Parser: a malformed record is skipped and counted per file by reason
  (`files.dropped`, shown by doctor); an unopenable file is not checkpointed and
  is retried; `utf-8-sig` handles a BOM; discovery walks without following
  directory links or junctions and lists each real file once; a missing `cwd`
  carries forward within a file and subagent files take their project dir.
- Titles: `titles` table, one row per session, rank custom-title > ai-title >
  last-prompt > first user message; a lower rank never replaces a higher one.
  **Removed the `prompts` table** (schema 6). Why: it kept every distinct
  last-prompt text of a session, up to 500 characters each, though only the
  title is ever shown (A-067). The latest stored prompt of each session became
  its title before the drop. The API still reports the title count as `prompts`.
- Timezone: `tzdata` on Windows; `zone_id()` keys a named zone by name, so DST
  no longer rebuckets. A zone change rebuckets stored rows in place without
  re-reading files; the CLI heatmap anchors on the report's today; stdout and
  stderr are UTF-8 when redirected.
- `day_start_hour` (config, `--day-start-hour`): `config.DayStartZone` shifts
  the day for every day computation; `clock_zone()` keeps hour-of-day figures on
  the clock. It is part of the sync identity, so a change rebuckets stored rows.
- Exports: every bucket carries `unpriced`; the CSV leaves `cost_usd` empty on a
  fully unpriced row and adds `unpriced_responses`.
- Wrapped reads through the caller's connection; a nested one deadlocked behind
  the caller's open write.
- Tests: `tests/test_parser_pricing_tz.py` (39) and
  `tests/test_mutation_guards.py`, the audit's d9 mutant killers moved into the
  suite. The `history.restore` guard was dropped with `history.py`.
- Also fixed: `README.md` still listed `--no-history`, removed in 6.5b.

#### Phase 6.5d: Quota sources and forecasting *(decided)*
**Do:**
- Layered quota sources, recorded in section 2 as the new order of preference:
  1. `vibewatt statusline` reads Claude Code's documented statusline JSON
     (`rate_limits.five_hour` / `seven_day`: `used_percentage`, `resets_at`).
     It appends a throttled sample, prints one line, chains the user's
     existing `statusLine` command, uses no network and finishes in < 50 ms.
  2. A read-only import of the desktop app's `plan-usage-history.json`,
     version-gated and deduped on `(org, t)`.
  3. `/api/oauth/usage` as a fallback only: at least 10 min between calls,
     backoff on 429 and never on page render. Parse any window generically.
- Quota becomes a list of windows `{key, label, scope, utilization, resets_at,
  source}`, per model where available. Resets are detected from the data,
  never from an assumed schedule. Stale dumps are not samples (A-013).
- Replace the two-sample burn rule (A-006, A-048). Each window shows a pace
  delta (used% minus elapsed%) and a P10-P90 projected-at-reset band from this
  account's past windows. Until enough windows exist it says "not enough
  history". An alert fires once per window episode. Spike alerts are
  time-scoped, not "every historical spike" (A-050). Alert days use the
  report tz (A-096). Meters render without local usage (A-036) and clamp
  `aria-valuenow` (A-083). Test A-053.
**Gate:** a burst-sampled series crossing 100% fires exactly once. A noisy
flat series fires nothing. The band never goes below the current value.
Statusline fixtures from Claude Code 2.1.80+ ingest correctly.
**Status (2026-09-22): done** on `fix/phase-6-5d-quota-forecast`. Record:
- Sources and sample schema: section 2.5. Schema 7 rebuilds `quota_samples`
  keyed on `(ts, key, scope)` with a `source`; old rows are kept as
  `source = 'legacy'`.
- **Replaced the two-sample burn rule** (`analysis/alerts.py`). Why: burst,
  whole-percent samples made its slope zero or wild, so it almost never fired
  (A-006) and fired on noise (A-048). `analysis/forecast.py` gives each window a
  pace delta and a P10-P90 band from how much every past window of the same
  kind still grew after the same elapsed fraction (at least 4 past windows),
  capped at 100 % for rolling windows. A quota alert is keyed on its window
  episode and fires once, when a window reaches 100 % or the median projection
  does.
- Spikes look back 24 hours with a baseline of the 50 priced responses before
  each (A-050); the active block comes from the rollup. Alert days use the
  report timezone (A-096).
- **Changed `vibewatt statusline`**: it no longer builds a report (month to
  date, block cost). Why: that re-read the store for every refresh of Claude
  Code's status bar; the command now records the plan windows from stdin,
  prints them and chains `statusline_chain`, well under 50 ms.
- `statusline_cache_path` dumps older than 10 minutes are ignored (A-013). A
  window whose reset has passed is dropped rather than shown as 0 %.
- `/api/quota` never calls the endpoint and returns pace, band, notes and the
  last 7 days of samples. Meters moved from `Hero` into `PlanMeters`, shown by
  Overview in every state (A-036), with `aria-valuenow` clamped (A-083).
- Real data: 594 desktop samples imported; the 7-day reset detected at
  2026-09-21 13:57 UTC matches the 99 % to 0 % drop in the file; the 5-hour
  window has 21 past windows and a band.
- Tests: `tests/test_alerts.py` (22), e2e
  `plan utilization stays visible when filters match no local usage`.

#### Phase 6.5e: Local security and API contract *(decided)*
**Do:**
- Security:
  - An ASGI middleware, first in the stack, allowlists Host
    `{127.0.0.1, localhost, [::1]}:<port>` (DNS rebinding, A-007).
  - Every non-GET request needs `Sec-Fetch-Site: same-origin` or a matching
    `Origin` and JSON content type (CSRF, A-004, A-047).
  - Warn on any non-loopback bind (A-103).
  - Neutralize CSV formula cells (A-072).
  - Size-cap every auto-fetched body (A-102).
  - Salt the repeated-read path hash (A-121).
- SQLite concurrency: WAL, `busy_timeout`, `BEGIN IMMEDIATE` writers, and
  migrations once at startup. Reads never 500 during a sync (A-056).
- Finish section 5:
  - `POST /api/sync` streams progress.
  - `/api/health` reports last sync, last harvest and coverage gaps.
  - `/api/quota` returns recent samples (A-005).
  - OpenAPI is complete and CI fails if `npm run gen:api` changes anything
    (A-033).
  - The filter dependency validates the same way everywhere (A-073).
  - Blocks list the active block first (A-074).
  - `surface` is a real dimension (A-075).
  - Extreme dates return 4xx, not 500 (A-028).
  - Alias and mask filters match what the API returns and mask covers
    sessions (A-029, A-030).
  - Hourly returns a matrix, or the spec says vector (A-115).
- Harvest:
  - Validate input (A-031).
  - Reject rows without an id (A-032).
  - A session without `cost_usd` is unpriced, not $0 (A-027).
  - Count only `environment_kind: anthropic_cloud` and Cowork remote. Drop
    ids that match a local `bridge-session`, so Remote Control sessions are
    not counted twice.
- Status uses `https://status.claude.com/api/v2/summary.json` with a
  3-second total timeout (A-095).
- Docs and tests: A-035, A-076. Fix the false README privacy section (A-061).
  List every outbound call in DATA-SOURCES (A-108). Remove the dead
  `--refresh` flag (A-078).
**Gate:** probe tests:
- Host `evil.example` is rejected.
- A cross-site `text/plain` POST is rejected and a same-origin JSON POST
  succeeds.
- A GET during a sync never returns 500.
- The OpenAPI diff check passes.
**Status (2026-09-22): done** on `fix/phase-6-5e-security-api`. Record:
- `api/security.py` `LocalOnly`, the outermost middleware: Host must be a
  loopback name (421 otherwise) unless `serve --host` names another. A
  non-GET needs `Sec-Fetch-Site: same-origin` (or `none`); without that header
  a present `Origin` must match the Host. The browser's verdict wins over
  Origin because Vite's dev proxy rewrites Host. A request with a body must be
  `application/json`. Requests with neither header (curl) are not browser-driven
  and pass. `serve` warns on any non-loopback bind.
- SQLite: `busy_timeout` 30 s; migrations run only when `meta.schema` is
  behind; sync commits after discovery and after each batch; a second sync
  gets 409 instead of queueing.
- `POST /api/sync` streams NDJSON progress with `Accept: application/x-ndjson`
  and returns JSON otherwise. `/api/health` is typed and reports `last_sync`
  and coverage (files per source, gaps of 7+ days, dropped records).
- `web/openapi.json` is now committed. `tests/test_security_api.py` fails when
  the live schema differs or a 200 response is untyped. This stands in for the
  CI step until Phase 7 adds CI: run `npm run gen:api` and commit both files.
- Filters: one validated dependency (dates 1970-01-01..9998-12-31, `from <=
  to`, literal `source` and `metric`); `/api/wrapped?year` is 1970..9998.
- Projects: `vibewatt/projects.py` maps raw names to shown names (aliases, then
  a mask numbered by all-time cost) for every response and resolves a shown
  name back for every filter. **Removed `cli.apply_aliases()` and
  `cli.mask_projects()`.** Why: each endpoint applied its own part, so shown
  names could not be filtered on and session endpoints leaked raw names.
- **Removed `breakdown/surface`.** Why: it was an alias of `breakdown/source`
  over local logs, while the surfaces that matter (web, Cowork remote) are the
  ones local logs cannot see. Sessions carry the real surface.
- **Removed the `serve --refresh` flag.** Why: it did nothing since Phase 3.
- `/api/blocks` returns the active block first, then newest first, with `limit`.
- Harvest: typed body (a list, `{data}` or `{ccr}`), 20 MiB cap, entries without
  an id rejected, sessions that ran locally (a local log or a non-cloud
  `environment_kind`) skipped, a session without `cost_usd` stored unpriced. The
  API and the CLI report per-reason counts; an input with no entries is an error.
- Status uses `summary.json` with a 3-second total deadline in a worker thread.
  Pricing download capped at 16 MiB, the quota response at 64 KiB.
- CSV export prefixes cells starting with `= + - @` with `'`.
- Schema 8: tool read path hashes are HMACs under a per-install key; old
  unkeyed hashes were deleted and are re-read from the logs that still exist.
- Legacy `/`, `/api/usage` and `/api/dataset` are hidden from the schema; Phase 7
  removes them with `ui.py`.
- Docs: README privacy rewritten (A-061); DATA-SOURCES lists every input and
  outbound call (A-108).
- Tests: `tests/test_security_api.py` (32).

#### Phase 6.5f: Analysis corrections, Wrapped and stats reconciliation
**Do:**
- Anomaly (7.4, spec change). The baseline uses active days only (no
  zero-fill) and the threshold is `median + 3 × max(1.4826·MAD, 0.1·median)`.
  Raw MAD with zero-fill flags every day for part-time users and gives 6-10%
  false positives on stable spend (A-041, A-042, A-120). "Not enough history"
  is correct and visible (A-043, A-093). Test A-045.
- Findings:
  - GET reads the stored snapshot and only `POST /api/analysis` recomputes
    (A-092).
  - Dismissal is keyed by rule and subject across filters (A-091).
  - Implement the route-backed finding-detail modal from section 6, or record
    the deviation (A-046).
  - Severity and "show dismissed" live in the URL (A-123).
  - Name the timezone (A-122).
  - Record the 7.6 cache-write deviation (A-119).
  - Test A-094.
- Wrapped: merge aliased projects (A-049), fix the year input (A-051), use a
  richer fixture and add tz tests (A-052, A-097, A-098), empty states (A-100),
  and widen the tests for 7.8/7.13 (A-099). KPI formula tests (A-077).
- **Stats reconciliation *(decided)*.** Next to the deduped totals, show
  "input + output (Claude Stats-equivalent)", plus a "why numbers differ"
  panel in Health/Overview. The desktop app and `/usage` Stats count every
  JSONL line without dedup, exclude cache, count messages as lines, include
  subagents in tokens and may be a stale snapshot. Show the raw-line figure
  only as a comparison, never as a headline. Define "session" in the UI
  (A-116, A-125).
**Gate:** a 2-days-a-week fixture yields zero anomalies and 30 flat days plus
one 10x day still yield exactly one. On the audit's real-data snapshot, the
reconciliation panel reproduces the desktop figures (33 / 21,183 / 15M) from
the raw-line rule.
**Status (2026-09-22): done** on `fix/phase-6-5f-analysis-wrapped`. Record:
- Anomaly: rule in 7.4. `anomaly.detect()` returns a status; the reasons it
  could not evaluate a day go to the notes and into the anomaly group itself
  (A-043, A-093). An unpriced day leaves the baseline instead of blocking the
  next 28 days.
- Findings: `analysis.current()` serves the stored snapshot until the store
  generation or the day changes; only `POST /api/analysis` forces a run
  (A-092). **Changed: old snapshot rows are deleted, not kept inactive.** Why:
  each filter selection left a permanent snapshot behind; nothing read them.
  Alert rows are kept, because a fired alert must stay known.
- Dismissals: schema 9 `dismissals` table. A session finding stays dismissed
  under every filter; other findings per selection (A-091). Existing dismissals
  were carried over.
- `GET /api/findings/{id}` and the route-backed modal
  `/analysis/findings/:id` (A-046). Severity and "show dismissed" are URL
  parameters (A-123). Peak findings name the zone (A-122).
- Stats reconciliation: `sources.stats_line()` is Claude's Stats rule (messages
  are user and assistant lines outside subagents; tokens are naive input +
  output of every assistant line, subagents included; no dedup; no cache; no
  Cowork). Sync stores it per file, UTC hour and session in `raw_lines`.
  `GET /api/reconciliation` and the Overview panel show it beside the deduped
  figures, with the reasons and the definition of a session (A-116, A-125).
  `scripts/reconcile_stats.py --until 2026-09-15T19:13:45+09:00` reproduces the
  desktop's 33 sessions, 21,183 messages and 14,964,413 tokens exactly.
- Wrapped: the year is typed into a draft and committed on Enter or blur while
  the previous year stays mounted (A-051); tables have empty rows and zero
  windows read as unavailable (A-100); aliased projects merge (A-049, done in
  6.5e). Tests with JST boundaries, a leap-day streak and three months of model
  mix with cache savings (A-052, A-097).
- Tests: `tests/test_analysis_wrapped.py` (12); the weekly summary is checked
  against a real store with a planted title (A-099); KPI formulas (A-077) and the
  cloud-twin case (A-098) are in `tests/test_mutation_guards.py`; e2e for the
  modal, URL state, the Wrapped year and the panel.

#### Phase 6.5g: Frontend correctness and accessibility
**Do:**
- Correctness:
  - The daily chart keeps the newest days on long ranges (A-037).
  - Show sub-cent costs as `<$0.01` (A-079).
  - Readable 422 errors (A-080).
  - Invalid URL dates degrade gracefully (A-082).
  - Paginate the remaining tables (A-086).
  - Coverage copy matches the data (A-087, A-088).
  - Footer links (A-081).
  - FilterBar handles errors (A-117).
- Accessibility and design:
  - Skip link and the heatmap is not 420 tab stops (A-040).
  - The sticky bar does not hide focus (A-038).
  - Contrast of `--tm` and error headings (A-062, A-084).
  - h1 and per-route titles (A-085).
  - Chart accessible names (A-090).
  - Tooltip tokens (A-089), no hard-coded colours (A-110) and `min-width: 0`
    everywhere (A-111).
- Tests: vitest for `lib/` (A-109) and theme plus dark-mode overflow e2e
  (A-112).
**Gate:** the existing e2e suite plus overflow at 1440/1024/768/390 in both
themes on every route. An axe-style contrast check passes for text tokens.
Vitest covers format, filters, dates and the error client.
**Status (2026-09-22): done** on `fix/phase-6-5g-frontend-a11y`. Record:
- Correctness: `lib/chart.ts` `barLayout()` keeps every day inside the daily
  chart (A-037); `fmtUsd` shows `<$0.01` (A-079); `api/client.ts`
  `errorMessage()` renders 422 lists as `field: message` (A-080); impossible
  URL dates are dropped by `isCalendarDay()` (A-082); the Overview daily table,
  the model table and both Wrapped tables use the paginated `DataTable` (A-086);
  the footer copy says which views are local-only and links to the source,
  DATA-SOURCES and issues (A-081, A-087); embedded sessions are headed by their
  dimension (A-088); FilterBar reports a failed options query (A-117).
- Accessibility: a skip link to `#main`; the heatmap is one `role="grid"` tab
  stop with arrow keys (A-040); `scroll-padding-top` under the sticky bar
  (A-038); an Overview `h1` and `useTitle()` per route (A-085); the hour chart is
  named and its tooltip uses theme tokens (A-089, A-090).
- **Changed: muted text and the footer use `--ts`, not `--tm`.** Why: `--tm` is
  3.7:1 on the light surfaces, below AA for the local-only disclaimers it
  carried (A-062). The token itself is unchanged, as DESIGN.md requires; it
  still colours disabled controls. Error headings are `--tp` with a
  `--serious` border (A-084). The backdrop is a token and the share card reads
  its colours from the tokens (A-110). More grids set `min-width: 0` (A-111).
- Tests: `src/lib/lib.test.ts` (8); `e2e/design.spec.ts`: overflow at four widths
  in both themes on eight routes, an axe-style contrast check that fails at
  3.71:1 when `.muted` goes back to `--tm`, the theme toggle, the skip link,
  heatmap keys, titles and the sticky bar (A-109, A-112).

### Repository cleanup for open source (2026-09-22)
**Why:** the repository is going public. It carried personal agent configuration,
audit scaffolding and dated phase notes that no longer matched the code.
**Record:**
- **Removed `.claude/`** (project settings, the gate and phase skills, the
  plan-reviewer agent) and gitignored `.claude/`, `.codex/`, `.agents/` and
  `docs/CODEX-SETUP.md`. Why: they configure one person's agent tools. Shared
  rules for every agent and contributor now live in `AGENTS.md`; `CLAUDE.md`
  imports it, so there is one copy.
- **Removed `docs/audit-2026-09-22/`** (the audit's repro probes). Why: each probe
  was turned into a maintained test during Phase 6.5 (`tests/test_dedup_store.py`,
  `tests/test_parser_pricing_tz.py`, `tests/test_alerts.py`,
  `tests/test_security_api.py`, `tests/test_analysis_wrapped.py`,
  `tests/test_mutation_guards.py`, `web/e2e/design.spec.ts`). The audit report
  itself stays.
- **Removed `docs/PHASE-4.md`, `docs/PHASE-5.md` and `docs/PHASE-6.md`.** Why: dated
  implementation notes, partly contradicted by Phase 6.5. What is still true
  moved into `docs/ANALYSIS.md`.
- **Removed `FEATURES.md`.** Why: a comparison matrix against four other tools with
  a stale score (A-105). The README lists the features.
- Moved `PLAN.md` to `docs/PLAN.md`. Added `CONTRIBUTING.md`, `docs/ANALYSIS.md`,
  a rewritten README and `.gitattributes` (LF line endings; the repository had
  mixed CRLF and LF).
- **History rewrite.** The removed paths were purged from every commit with
  `git filter-repo`; every branch was force-pushed. Commit ids before this
  point changed. PRs #2 to #10 were closed in favour of the merge of this cleanup
  into `master`, which contains all of their commits. A mirror of the old remote
  and a bundle of every local ref were kept outside the repository.

### Phase 7 — Packaging and polish
**Status (2026-09-25): implemented and verified locally** on
`feat/phase-7-packaging-polish`. Independent review found no remaining correctness
or Phase 7 requirement gap. Publishing and hosted CI execution are separate.

- React ships in the wheel and sdist. `app.frontend()` serves deep links with
  explicit MIME types and the local-only guard outermost. Missing APIs and assets
  stay 404; a source checkout without assets shows 503.
- Build validation requires hashed JS and CSS, checks index references and
  excludes source maps. `scripts/check_build.py` verifies incomplete source copies
  and source-map exclusion without modifying the working build.
- Route loading preserves separate modal boundaries. Vite 8 splits charts into
  a 488.10 kB chunk; no chunk warning remains. npm audit reports zero findings.
- The browser suite uses the production FastAPI static server instead of Vite
  preview. It passes 47 checks including both themes, four widths and deep links.
- CI on Windows and Linux checks Python, frontend, OpenAPI drift, dependency
  floors, build guards and the installed dashboard. No hosted run is claimed.
- README screenshots use fixture data from clean wheel installs. The actionable
  backlog and prerequisites are in [DEFERRED.md](DEFERRED.md).

Verification output:

```text
uv run pytest -q
267 passed in 13.03s
Python 3.11 with --resolution lowest-direct
267 passed in 12.84s
uv run ruff check .
All checks passed!
uv run ruff format --check .
69 files already formatted
npm run test
Test Files  2 passed (2)
Tests  12 passed (12)
npm run e2e
47 passed (51.0s)
uv build
Successfully built dist/vibewatt-0.3.0.tar.gz
Successfully built dist/vibewatt-0.3.0-py3-none-any.whl
PASS release guard: complete
PASS release guard: missing-css
PASS release guard: missing-index
PASS win32: clean wheel, 11 assets, SPA, APIs, fixture data, no Node.
PASS linux: clean wheel, 11 assets, SPA, APIs, fixture data, no Node.
PASS installed dashboard: six routes, both themes, session deep link, no runtime errors
```

Post-commit clean-snapshot verification reran all 267 Python tests, 12 frontend
unit tests and 47 browser checks. It caught a verification-script resource leak:
`sqlite3.Connection` context management commits but does not close the handle.
The idempotence probe now closes it explicitly so Windows can clean its temporary
database. This changes only the gate script, not the application or stored data.

The wheel checks use fresh pip-installed venvs in paths with spaces and Japanese
characters, launch outside the checkout and hide Node from the server PATH.
Sync idempotence and doctor use fixtures. Installed checks ran on Windows Python
3.13 and WSL Ubuntu Python 3.14; Python 3.11 was tested separately. The browser
controller remains on the build host. An initial floor probe omitted installing
vibewatt and failed two subprocess tests; the corrected `--with-editable .` probe
passes all 267. The reviewer independently reran pytest, Ruff, the distribution
build and negative hook cases. Disposable `.tmp-review-dist` output remains
ignored locally because command policy blocked cleanup.

Name check: PyPI has the user's 0.0.1 reservation, GitHub is
`MMALI3287/vibewatt` and npm returns 404. Nothing was published or reserved here.
References: [FastAPI, 2026](https://fastapi.tiangolo.com/tutorial/frontend/),
[Vite, 2026](https://vite.dev/guide/build) and
[Vitest, 2026](https://vitest.dev/guide/migration/).

**Toolchain continuation (2026-09-25):** Vite 8, plugin-react 6, Vitest 5,
React Router 7.18.4, TypeScript 5.9 and openapi-fetch 0.17.0. Removed
`react-router-dom` in favour of `react-router`, its supported unified imports.
Removed the dev `httpx` dependency in favour of `httpx2` for Starlette's current
TestClient. Python now requires 3.11, FastAPI 0.141.1 and uvicorn 0.53.
The Router 7 transition makes checkbox URL updates asynchronous; the browser
check now clicks and waits for the controlled checked state instead of asserting
it inside Playwright's synchronous `check()` operation.
**Removal record (2026-09-25):** removed `vibewatt/ui.py`, the `html` CLI
command and `/api/usage` and `/api/dataset`. React and the typed `/api/*`
endpoints supersede the unsafe embedded-data renderer (A-054, A-126).
Replaced the legacy-route success test with assertions that retired routes
return 404. Removed redundant imports, UTC alias assignments and casts during
lint cleanup; they carried no behaviour. No stored data or configuration was removed.
**Do:** build directly into `vibewatt/static` and ship it in the wheel. `vibewatt serve` opens the React app. Windows path tests. Docs. Screenshots in the README.
Added 2026-09-22 *(decided unless noted)*:
- Name: re-check that `vibewatt` is free on PyPI, npm and GitHub. With the
  user's go-ahead, reserve the names; publishing is outward-facing.
- Toolchain first, as its own commit: Vite 8, `@vitejs/plugin-react` 6,
  Vitest 5, react-router 7.18.4 (import from `react-router`). Pin TypeScript
  to 5.x for openapi-typescript and pin openapi-fetch 0.17.0 exactly. Then
  split chunks with Vite 8 `codeSplitting` and route-level `lazy`. `npm audit`
  must show no high/critical findings in runtime dependencies (A-118, A-127).
- Remove `vibewatt/ui.py` and the `html` command and record why: the React app
  supersedes them and they carry XSS sinks (A-054, A-126).
- `requires-python >=3.11` (3.10 reaches end of life on 2026-10-31).
  `fastapi>=0.141.1` (strict JSON content type, `app.frontend()`),
  `uvicorn>=0.53`. Dev group: `httpx2`, plus a narrow filter for the anyio
  deprecation warning. Test the floors with `--resolution lowest-direct`.
- Serve the SPA with `app.frontend()`. Register JS/CSS/SVG/woff2 MIME types
  explicitly, because the Windows registry can map `.js` to `text/plain`.
  The build hook requires `index.html` plus hashed JS and CSS. Exclude `.map`
  from the wheel. The sdist includes `static/`.
- Docs refresh: section 1 and 3 drift and repository-wide lint and format debt
  (A-106, A-107, A-128). The README, the agent commands (A-104) and FEATURES.md
  (A-105) were handled by the 2026-09-22 repository cleanup.
**Gate:** `pip install dist/*.whl` in a clean venv on Windows and Linux, then `vibewatt serve` renders the dashboard with no Node present.

---

### Post-phase deferred essentials (2026-09-26)

The user authorized all eight items assessed as ready after Phases 1-7.
These additions do not reopen the completed phases.

- Stored local costs now follow the loaded pricing table and configured
  overrides. A pricing fingerprint covers built-in, remote, fast, geographic
  and tool rates plus the algorithm version. `sync_store` reprices retained
  turns even when their logs are unchanged or gone. A savepoint updates costs,
  affected rollups and generation together. Cloud-reported costs stay intact.
  The fingerprint is store-wide because every retained turn is evaluated in
  one atomic pass; per-turn schema expansion is unnecessary. Existing `meta`
  stores the fingerprint and last-reprice timestamp. No migration is required.
- `timezone: local` resolves the OS timezone with `tzlocal`, which is a new
  dependency because portable standard-library discovery does not map Windows
  timezone names to IANA rules. Winter and summer use their historical offsets.
- `/api/report-context` supplies the shifted report date, named timezone,
  day-start hour and last-sync timestamp. Date presets use that calendar date.
- Overview compares current month-to-date local API-equivalent cost with the
  configured full monthly plan fee. It respects source/project/model filters
  but explicitly ignores the selected date range. It is neither actual savings
  nor a bill. Invalid/unset plan prices omit the comparison; unpriced turns
  suppress the multiple.
- Header search submits to existing stored session search (title, project and
  model). `/` focuses it except while typing into editable controls.
- One browser timer refreshes active store-backed queries every 60 seconds
  while visible and online. It excludes external status, paid generation and
  sync mutations. Infinite session lists pause polling after the first page
  to bound repeated work. Cached quota reads do not fetch external usage.
- Provenance distinguishes computed local usage, estimated local cost,
  cloud-reported snapshots and official quota readings. Shared labels apply
  to groups of figures with the same origin. Local `as_of` means last sync;
  cloud session `as_of` is its reported update time; mixed Wrapped data exposes
  separate local sync and cloud harvest times. Missing timestamps stay unknown.
  Analysis generation time is labelled separately from source freshness.
- `status --json` and `quota --json` are schema-versioned store snapshots.
  They do not sync logs or fetch network data. See `docs/AGENT-JSON.md`.

Removal record: the eight completed rows were removed from the active table
in `docs/DEFERRED.md` because this implementation resolves them. Their history
is retained in that file and here. Fixed-offset local timezone resolution was
replaced because it misbucketed historical DST dates. No service, stored table,
legacy command or user data was removed. The old dashboard refresh script
remains removed; browser refresh is implemented in React.

The remaining 17 items and the release-scope choices were decided on
2026-09-26. See "Phase 8" below and the Phase 8 build plan (removed in Phase 11).

**Checks:** Python regression suite, Ruff check/format, frontend unit tests,
production build, generated OpenAPI/client types and browser checks including
the 60-second polling cycle, header search, presets, both themes and widths.

Verified 2026-09-26: 292 Python tests, 15 frontend unit tests and 51 Chromium
checks passed. Ruff check and format checks passed. A freshly built wheel was
installed outside the checkout on Windows with Node absent from the runtime
PATH; fixture sync, assets, APIs, all six dashboard routes, both themes and a
session deep link passed without runtime errors. Linux/macOS clean-install
checks were not rerun for this addition. No release was published.

Pre-merge review fixes (2026-09-26). A three-reviewer pass over the unmerged
Phase 7 and post-phase diff, a follow-up review of the fixes and the first CI
run found these defects. Each fix has a regression test:

- `timezone: local` crashed every command, statusline included, when `tzlocal`
  raised `ZoneInfoNotFoundError` for a POSIX `TZ` string or conflicting system
  configs. It now warns and falls back to the current UTC offset.
- A stale pricing cache while offline loaded an empty remote table. That changed
  the fingerprint and repriced remote-only models (Sonnet 3.5/3.7) to unpriced.
  `refresh()` now falls back to the stale cache.
- Restored `history.json` days priced their missing part as history cost minus
  the repriced store cost, which could reach $0. The missing tokens are now
  priced at current rates.
- `status --json` never loaded the cached remote table, so restored history
  disagreed with `vibewatt json`. It now reads the cache without fetching.
- React Router 7 commits history in a transition. URL-backed inputs (session
  search, date fields) lost keystrokes. `BrowserRouter` now sets
  `useTransitions={false}`. Without transitions a lazy route would show its
  Suspense fallback on first visit, so route pages use `lib/preloadable` and
  their chunks are warmed when the browser is idle.
- The first CI run failed on Linux only. The e2e fixture server patched
  `app.state.tz` after `create_app` had captured the machine's local zone, so
  results depended on the runner's timezone. The zone is now set in the config
  before `create_app`. CI runs with `fail-fast: false` so one platform's
  failure no longer cancels the other.
- The Windows wheel gate failed intermittently while deleting its scratch
  directory. pip's console-script launcher exits before the Python server it
  starts, so the server still held `server.log` for about a millisecond after
  `process.wait()` (measured in 5 of 5 local trials; longer on CI runners).
  Cleanup now retries until the handles are released, bounded at 15 seconds.
- The Phase 8 handoff cited a `vibewatt reprice` command that does not exist.
  Repricing runs inside `sync`.

---

### Phase 8: Backlog completion (completed 2026-10-01)

Final implementer check (2026-10-01): replaced the first-nonempty geo merge
because duplicate `global` and `us` observations produced different stored
costs depending on block or sync order. Geo now retains the highest observed
surcharge with deterministic ties. Six regression cases cover permutations,
separate syncs, rollup cost and repair from unchanged logs. Old file checkpoints
are invalidated once through a dedup version so retained logs can restore geo
evidence discarded by the old merge. Checkpoint rows and retained usage remain;
missing transcripts cannot supply lost evidence and are never guessed.
Stopped the verified leftover local Playwright fixture server because its
listener on port 8778 prevented a fresh browser gate from starting.

The corrected-code local gates passed: Ruff, 359 Python tests, 35 frontend
tests, 63 browser checks, the production build, unchanged generated API files
and zero production dependency advisories. A fresh Windows wheel passed
Node-free serving, the original 0.3.0 database upgrade, backup integrity and
the installed dashboard browser checks. A temporary copy of the real store
synced offline from 9,383 responses / $2,497.529639 to 9,824 responses /
$2,581.702276 after discovering newer logs. The repeat parsed zero files and
left every reported total unchanged. Original databases were opened read-only.
The baseline master already passed every CI step on Linux, Windows and macOS;
the correction requires the same exact-head CI gate before integration.
Claude's independent Phase 9 audit remains the next phase.

#### Package 5: platform coverage and readiness (2026-10-01)

- Removed the macOS CI and three-platform clean-install deferred row because
  the quality matrix now runs Windows, Linux and macOS. The existing lowest
  direct dependency lane remains intact. The empty Phase 8 deferred table was
  removed because every active item is implemented or explicitly closed below.
- The installed-wheel gate seeds a synthetic schema 9 store generated using
  the original 0.3.0 store implementation. It checks preserved priced/unpriced
  turns, rollups, harvested cost, Japanese titles, history and a custom table;
  schema 12 attribution; database integrity; and the automatic schema 9 backup.
  These checks run again after serving the installed wheel with Node absent.
- The same gate checks repeat sync, API data, assets and MIME types, SPA deep
  links and an actual installed-dashboard browser cycle in both themes. Local
  Windows verification passed; Linux and macOS are verified by the CI matrix.
  Merge the close-out PR only after every platform passes at its exact head.
- macOS CI exposed a fixture portability bug: pytest and browser fixtures
  placed Cowork data beneath Windows application data while Darwin discovery
  searches its native directory. Both fixture entry points now use a separate
  temporary `cowork` root with an explicit override. The former application-data
  fixture location and override clearing were replaced because they either
  missed Darwin data or discovered the same files twice on Windows and Linux.
  Production discovery is unchanged. Regressions verify all three platforms and
  exercise the actual browser-server setup without starting a server.
- Final local checks: 353 Python tests, 35 frontend unit tests and 63 browser
  tests. Ruff, generated API stability, build and production audit pass. A
  whole-phase independent development review found an import-order duplicate
  and retained SQLite readers; four failing regressions now pass after fixes.
- Temporary verification servers and the unused duplicate managed checkout
  are stopped or archived after verification because the reused Phase 8
  checkout holds the implementation. Verification scratch profiles and stores
  remain private and are not distributed. Merged package branches can be
  removed after their implementation is retained on master.
- The completed implementation handoff replaces the earlier "current Phase
  8" guidance. Phase 9 is the next independent audit. No Phase 9 implementation,
  provider work, version bump, tag, release or publish is included here.

#### Package 4: dashboard export and browser notifications (2026-10-01)

- Removed the PNG export and browser notification deferred rows because both
  dashboard features are implemented. Export captures the selected view, active
  filters, theme and provenance footer. Project names are masked by default;
  including them requires an explicit checkbox. Missing masking metadata stops
  the export instead of exposing project names.
- The `html-to-image` dependency serializes the full HTML view because tables,
  filter controls and the provenance footer cannot be captured by serializing
  only the existing SVG charts. Export dimensions are bounded before rendering.
- Notifications remain off until the user opts in and browser permission is
  granted. Warning and serious alerts use persisted account-scoped alert IDs
  and a six-hour cooldown per kind. Hidden or offline tabs do not deliver them.
  There is no service worker, push service or webhook.
- Observed a native Edge notification on Windows using the dashboard's opt-in
  control in a disposable browser profile and synthetic alert data. The native
  `show` event confirmed delivery. Reloading the same alert and then a different
  alert of the same kind left the delivery count at one within the cooldown.
- Whole-phase review found an import-order overlap: harvested data followed by
  foreign-machine local turns could count a session twice. Shared original-ID
  matching now suppresses the cloud row in Sessions, Wrapped and later harvests
  in either order; existing cloud deep links resolve to local evidence. The old
  transfer-only prefix stripping was replaced because it protected only one
  import order. Read-only identity probes now close SQLite handles explicitly
  so Windows can relocate a database immediately after probing it.

### Package 3: accounts and machines (2026-10-01)

- Removed the multi-account/multi-machine deferred row because account selection,
  persistent machine UUIDs and versioned local export/import are implemented.
  Separate account databases prevent account mixing across every report path.
- Schema 12 takes an automatic SQLite backup and replaces the old `turns`
  definition with a machine/account-aware primary key after copying every row.
  Existing indexes are recreated, including the indexed hourly lookup.
  Unattributed quota samples are copied into `unattributed_quota_samples` before
  removal from active quota history so an identified account cannot adopt them.
- Transfers preserve per-field maxima and are idempotent. Archives omit raw
  session data, credentials and quota; titles require `--include-titles`.
  Both compressed and expanded input are capped at 200 MiB and 500,000 total
  records, including titles. Failed temporary exports are discarded so they
  cannot replace an existing archive.
- Observed real cycle on a private store copy: 8,338 responses, 4,793,742 expanded
  bytes and $2,368.99880125 before/after. Repeat import changed zero rows.
  A reduced 10-record cap rejected the archive before writing.


**Do:** build packages 1-5 in the Phase 8 build plan (removed in Phase 11), which holds the
scope, bounds and acceptance checks for each: pricing completeness, local context
nudges and `history.jsonl` activity, multi-account and multi-machine
export/import, PNG export and browser notifications, then macOS CI and
three-platform clean-install checks.
**Out of scope:** new providers (Phase 10), version bump, tag, GitHub release
and PyPI publish (Phase 11).
**Gate:** every package passes the CI gate. Package 5 passes on Windows, Linux
and macOS. `DEFERRED.md` lists only Phase 10 items afterwards.

Decisions (2026-09-26). The user chose: release = tagged GitHub release plus
PyPI publish; platforms = Windows, Linux and macOS; build multi-account and
multi-machine support; browser-only opt-in notifications for warnings and above;
close cloud listing, localization and commit links; ChatGPT/Codex and
Gemini/Antigravity providers after verification. The remaining calls below were
made from the local log evidence recorded in the Phase 8 build plan (removed in Phase 11).

Closure record. These items were removed from the active backlog in
`docs/DEFERRED.md` in this change. Each reason is recorded with a reopen trigger.

- **Advisor-model pricing.** No advisor iterations exist in 18,049 real
  `usage.iterations[]` entries, so overlap with top-level usage and advisor rates
  cannot be verified. Phase 8 adds a `doctor` detection guard instead. Reopen when
  the guard fires on real data.
- **Opt-in cloud session listing.** User decision. `/v1/code/sessions` is
  undocumented and needs credentials. File harvest stays supported. Reopen when a
  documented endpoint exists.
- **Optional OTLP receiver.** JSONL already supplies every attribution field the
  reports use. A receiver adds a local network surface for no current gain. Reopen
  when a needed field exists only in OTLP.
- **Localization.** User decision: English-only release. Reopen when a named
  language has a terminology reviewer.
- **Drain explainer and metering drift.** Needs months of quota history with
  version provenance. On thin history it would imply plan limits the data cannot
  support, which section 9 forbids. Plan meters already show quota. Reopen at 90
  or more days of retained quota history.
- **Spend linked to commits and finding follow-up outcomes.** User decision on
  commit links. Finding outcomes were the same backlog item and share its
  causation risk. Reopen when the user asks for either.
- **React 19, Router 8 and TanStack Table 9.** No concrete compatibility or
  security need. CI runs `npm audit --omit=dev --audit-level=high`. Reopen on an
  advisory, end of life of a current major or a blocked dependency.
- **Webhooks and desktop (OS) notifications.** User decision: browser
  notifications only. No webhook secret storage is needed.
- **npm name reservation.** Not needed for a Python wheel. Reopen if a
  JavaScript package is planned.

Rescheduled, not closed: Codex, Gemini and other agent CLI ingestion moved from
the backlog to Phase 10. No code, table, command or dependency was removed by
this planning change.

Package 1 implementation (2026-09-26): removed the five pricing rows from
`DEFERRED.md` because retained repricing now has retired Sonnet and Mythos
Preview rates, dated fast-mode lookup, verified historical context premiums,
usage counters and an advisor detection guard. Unsupported historical intervals
remain explicitly unpriced or flagged, as required by the no-guessed-rates rule.
Schema 10 adds counters with a pre-migration SQLite backup and invalidates file
checkpoints once to backfill retained logs. No usage rows were removed.

Validation also found a pre-existing test calling `monkeypatch.undo()` before an
API request, which undid the home/data isolation. Replaced the broad undo with
restoration of only the patched function. The live store migrated during the
first run; its automatic pre-v10 backup had identical usage rows and was restored
to schema 9 for installed-version compatibility. The migrated copy was preserved
privately. No user usage was removed. Copied-store JSON comparison: 8,338 responses,
$2,368.998801 before and after, with zero fast rows and zero unpriced responses.

Package 2 implementation (2026-09-30): removed local context-window nudges and
`history.jsonl` activity backfill from the active deferred table because both are
implemented. Schema 11 adds a separate activity table with a pre-migration backup.
The prior harvested-only context restriction is superseded by the latest
main-thread prompt snapshot for sessions active within 30 minutes. The 70%/85%
thresholds remain unchanged; 1M capacity requires a model marker or prior
per-response evidence in that session. Quota readings never enter this calculation.
The activity collector accepts only timestamp/project, deduplicates both and caps
each sync at 50 MB / 500,000 records. Calendar and streak are separately labelled
and add no usage or cost. An observed real cycle read 145 records / 40,638 bytes;
a repeat inserted zero and a 10-record cap stopped at 10 with truncation reported.
No service, command or usage data was removed.

### Phase 9: Verification (completed 2026-10-05)

**Do:** an independent audit of Phase 8 against the Phase 8 build plan (removed in Phase 11) and
`AGENTS.md`: rerun every gate, check each acceptance item against real behavior,
confirm new functions have real callers and fix findings test-first.
**Gate:** every Phase 8 acceptance box is verified, not only ticked.

Audit record (2026-10-05, Claude Code):

- Gates rerun on `master` at d2fe0be before any change: Ruff, 359 Python tests,
  35 frontend tests, build, generated API (no content diff), production audit
  and 63 browser tests all pass. The last master CI run passed on Linux, Windows
  and macOS.
- Packages 2-4 were read against their acceptance checks: history parsing keeps
  only timestamp and project under the 50 MB / 500,000 record cap, transfer
  validates the whole bounded archive before opening a writable store, and
  browser notifications filter to warning and serious alerts with per-kind
  cooldown. Code execution and unverified context premiums are shown on the
  Overview, not only in JSON.
- **Finding 1 (package 1.1): current models unpriced.** `claude-opus-5-5` (from
  2026-09-23) and `claude-sonnet-5-5` (from 2026-10-01) were in retained data
  but not in `BUILTIN`. A copy of the real store had 2,439 of 11,395 responses
  unpriced and cost reported as $2,467.38. Opus 5.5 was seen before Phase 8
  closed, so the "every model seen in retained data" check missed it. The copy
  was synced with `--offline` and a pricing cache last written 2026-09-15. An
  online sync would have filled both rates from the LiteLLM table, which lists
  them correctly; the built-in table is still the required anchor. Added both
  rates and Opus 5.5 fast mode from its 2026-09-24 release-note date. Both ids
  joined the long-context exemption. After repricing: 11,411 responses, 0 unpriced,
  $2,788.28 (the 16 extra responses were written during the audit).
- **Finding 2 (package 1.3): false `context_premium_unknown`.** The same 1,834
  rows were flagged as having an unverified premium although the official page
  states standard pricing across 1M for these models. Now 0.
- **Finding 3 (package 2.1): context nudges used 200K for native-1M models.**
  Claude Code documents that Opus 4.7+, Sonnet 5+ and Fable run a 1M window on
  every plan on the Anthropic API. The nudge assumed 200K until a session crossed
  200K, so it warned at 140K, 14% of the real window, on almost all real usage.
  First-party ids of those models now use 1M. Provider ids (Bedrock, Vertex) and
  `[1m]`-only models keep the old evidence rule. The Opus 5.5 and Sonnet 5.5 ids
  were also missing from the nudge model list, so nudges never fired for them.
  A test now requires every priced model to have a context window entry.
- Orphan sweep: no Phase 8 function lacks a production caller. Three older
  functions with none (`terminal.hour_histogram`, `alerts._time` and the
  test-only `store.upsert_quota_samples` wrapper) were removed in a separate
  cleanup PR. Their removal records follow this audit record.
- Branch protection now requires `verify (macos-latest)` alongside the Linux
  and Windows checks (user approved 2026-10-05). The job already ran on every
  push, so this adds no CI time.
- Closed: every Phase 8 acceptance item was rerun against real behavior. The
  three findings are fixed in #19, dated pricing is in #20 and the dead code
  is removed in #21.
- Dated pricing (2026-10-05, user request): community rates are now recorded
  as dated changes in `pricing-history.json`, so a discount or price change
  applies only from the day it is observed instead of repricing all history.
  `BUILTIN_PERIODS` holds verified dated built-in rates. `doctor` warns on a
  community table older than 7 days. A weekly drift workflow opens one issue
  when built-in and community rates disagree. Models newer than the built-in
  table use the community context window for nudges. Its first live run flags
  `claude-mythos-preview`: built-in $25/$125 from Anthropic's Glasswing page
  versus community $10/$50. Anthropic's page, rechecked 2026-10-05, still
  states $25/$125, so the built-in rate stays and the drift check ignores that
  exact community value as a known community error.
- No file, table, flag or dependency was removed in this change.

Dead code found by the caller sweep (2026-10-05). Each function was grepped
across `vibewatt/`, `web/src`, `tests/` and `scripts/` before removal:

- **Removed `terminal.hour_histogram()`.** Why: no caller anywhere. No CLI
  command or report rendered it, so it was untested output nobody could see.
- **Removed `analysis.alerts._time()`.** Why: no caller anywhere. Alert code
  converts instants inline, so the helper was a leftover.
- **Removed `store.upsert_quota_samples()`.** Why: a one-line wrapper over
  `quota.record()` whose only callers were two tests in `tests/test_store.py`.
  Production writes quota samples through `quota.record()` directly. Those tests
  now call `quota.record()`, so they cover the path production uses.

### Phase 10: More providers (completed 2026-10-06)

**Do:** Gemini CLI/Antigravity usage and ChatGPT/Codex CLI usage, including
the plan rate-limit readings that Codex logs carry. Each provider is one `ingest/` module
with `discover()` and `parse()`. Before parsing, `DATA-SOURCES.md` records its
response identity, whether counts are cumulative or per-turn, model rates with
cited sources and retention. Provider stats are labelled local-only and kept
separate by `turns.source`.
**Gate:** order-independent dedup on sanitized fixtures, no double counting of
cumulative counts, one observed real sync per provider with local data.

Codex record (2026-10-05, Claude Code). The log contract, rates, plan readings,
identity and gate evidence are in `DATA-SOURCES.md` under "Codex".

- Added `ingest/codex.py` (`discover`, `parse`, `plan_readings`), source `codex`,
  OpenAI rates in `BUILTIN_PERIODS` with release-dated windows, the 272K
  long-context rule and a flag for unverified Sol prompts.
- **`codex-auto-review`, `gpt-5.4` and `gpt-5.6-terra` are priced.** The first
  draft of this phase left them unpriced. Terra and 5.4 have model pages; the
  auto-review slug maps to GPT-5.4 per OpenAI's auto-review report. No Codex
  response in the real data is unpriced now.
- **Scopes.** `all` is every provider with a per-source split. New `claude` scope
  (`--source claude`, "All Claude surfaces" in the filter) covers Claude Code,
  Cowork and web. An intermediate draft made `all` Claude-only; the user rejected
  that because it hid Codex from the default view. Claude-specific features
  (findings, context nudges, alert baselines, the 5-hour block, the plan-price
  comparison) read Claude rows under any scope (`sources.CLAUDE_ONLY`).
- **Codex plan readings are imported** into `quota_samples` under a
  `chatgpt:<account>` scope, stored only when a window changes. Every Anthropic
  plan reader excludes that scope (`quota.CLAUDE_SAMPLES`). They show in the CLI,
  `doctor` and a separate dashboard meter group (`/api/codex-quota`).
- **ChatGPT account id** comes from `auth.json` `tokens.account_id`
  (`identity.chatgpt_account_id()`). It scopes plan readings. Usage rows keep the
  Claude identity because rollouts do not name their account.
- **Removed the single "store now holds N responses $X" line from `sync`.** Why: it
  blended two providers into one dollar figure. It now prints one line per source.
  `store.summary()` is no longer called from `sync`; the API and status still use it.
- **Changed the doctor streak check** to count Claude days only. Why: a Codex day
  is not Claude activity. The streak message explains Claude surfaces.
- **The 5-hour block now counts Claude rows only.** Why: it models Anthropic's
  plan window; Codex usage inside it would overstate that window.
- Known limits: fast tier and cache writes are unobserved in local data; a
  ChatGPT account switch is not attributed to past usage rows.
- Gemini Antigravity and GitHub Copilot were wrongly recorded as having no local
  data. `~/.gemini/antigravity` and VS Code Copilot Chat sessions exist. They are
  the next Phase 10 slices; see `DEFERRED.md`.

Copilot record (2026-10-05, Claude Code). Contract and evidence are in
`DATA-SOURCES.md` under "Copilot".

- Added `ingest/copilot.py`: journal replay, one response per request, billed
  credits as cost, plan readings from Copilot's entitlement cache.
- **Schema 13 adds `turns.billed_usd`.** Why: a provider-billed amount must not
  be recomputed from rates on repricing. Existing rows get NULL; no usage changes.
  Transfer archives without the field still import.
- Quota readers now exclude every other provider's scope (`chatgpt:`,
  `github:`) through `quota.PROVIDER_SAMPLES`.
- Pricing: GitHub-listed rates for three Copilot-only models from 2026-06-01, and
  OpenAI `-YYYY-MM-DD` snapshot suffixes normalize to the family id.
- **Replaced `cli.codex_quota()` with `cli.provider_quota(provider)`.** Why: the
  same plan block now serves Codex and Copilot.
- No file, table, command or dependency was removed.

Antigravity record (2026-10-05, Claude Code). Contract and evidence are in
`DATA-SOURCES.md` under "Antigravity".

- Added `ingest/antigravity.py`: read-only SQLite, one-level protobuf decoding by
  known field numbers, step times by response id, size and plausibility caps.
- `gemini-3.8-flash` rates with the promotion window. `pricing.ALIASES` maps
  Antigravity's `-n` and `-high` variant ids to it (assumption recorded).
- Sync detects change from the database plus its `-wal` file for this source.
- **Read-tool metadata and title scans now run only on Claude transcripts.** Why:
  they read every file as text lines; on a binary database or another
  provider's log they find nothing and waste a full read.
- No file, table, command or dependency was removed.

Phase 10 closure (2026-10-06, Claude Code).

- **Gemini CLI is not imported.** This machine has no Gemini CLI install and no
  `~/.gemini/tmp/*/chats` sessions, so there is nothing to observe and the gate
  ("one observed real sync per provider with local data") cannot be met. A parser
  written blind would guess its contract. Antigravity, the Gemini surface that
  does have local data, is imported. Recorded in `DEFERRED.md` with its trigger.

### Phase 11: Public launch

**Do:** make the GitHub repository public for contributions, bump the version,
write release notes, tag, create the GitHub release and publish to PyPI.
**Gate:** each external step (visibility change, tag push, release, PyPI upload)
is confirmed by the user separately, with the exact version and artifact hashes.

**Gate change (2026-10-06):** the user instructed "after phase 10 is fully done
dont wait for any of my decision finish phase 11 properly". That standing
instruction replaces the per-step confirmation. The version and hashes are still
reported in the release notes and in the session.

Phase 11 record (2026-10-06, Claude Code).

- **Version 0.4.0.** 0.3.0 was never published (PyPI holds only the 0.0.1
  reservation). Phase 10 added three providers, so this is a minor bump. The
  version lives in `pyproject.toml`, `vibewatt/__init__.py` and
  `web/package.json`. The release workflow refuses a tag that disagrees with any.
- Added `CHANGELOG.md`, `.github/workflows/release.yml` (tag-triggered build and
  checks, PyPI trusted publishing, GitHub release with SHA-256 sums) and the
  release procedure in `CONTRIBUTING.md`.
- CI checks the built artifacts by glob instead of the literal `0.3.0` file
  names, under bash because pwsh on Windows runners does not expand globs.
- **Pre-public history scan.** All 49 commits were searched for API keys, OAuth
  and GitHub tokens, PyPI tokens, AWS keys, JWTs and private keys. None were
  found. Author email and name appear as intended. Real project names
  (`demo-app`, `DemoLedger`) and one real Antigravity conversation id were
  replaced in the current tree. They remain in history, where they grant no
  access.
- **Fixed a real-data leak in the e2e fixture server.** It redirected `APPDATA`
  but not `LOCALAPPDATA`, so on Windows `/api/copilot-quota` served the
  developer's real Copilot cache. Found when the demo server showed a real
  reading. `isolate()` now covers it and `tests/test_server_isolation.py`
  asserts every provider path resolves inside the sandbox.
- Added `scripts/demo_server.py` (seeded synthetic six-week store) and retook
  the README screenshots from it. The old captures showed a 2-response fixture.
- README now covers Codex, Copilot Chat and Antigravity, the full `--source`
  list and `export`/`import`. **Removed the "Source release" note** that said
  PyPI held only the reservation. It is false once 0.4.0 is published.

Phase 11 outcome (2026-10-06).

- PR #26 merged as `c6315fb` after CI passed on Linux, Windows and macOS.
- The repository is public, with description, topics, secret scanning, push
  protection and vulnerability alerts on. GitHub's secret scan found 0 alerts.
- Tag `v0.4.0` on `c6315fb`. The release workflow built and checked the
  artifacts and created the GitHub release. SHA-256 of the published files:
  `8ddb95c7088164eeeee757aa13c8dfc669ef51679071a1e29ecfb057dca831c7` (wheel),
  `22a077088ee17047d4bf12fd8a9fe7dfee2b8da007d42a90bb785e41994f8f98` (sdist).
- **PyPI upload.** The first `pypi` job failed with `invalid-publisher`
  because PyPI had no trusted publisher for this workflow. The owner added it
  (owner `MMALI3287`, repository `vibewatt`, workflow `release.yml`, environment
  `pypi`) and the job was re-run. PyPI serves 0.4.0 with the SHA-256 sums above,
  and `uvx --from vibewatt==0.4.0 vibewatt --help` runs from a clean cache.

Open-source readiness (2026-10-06, Claude Code).

A sweep of phases 1 to 11 found every phase gate recorded and passing (464
pytest, 35 vitest, production build) and no stubs or TODO markers in the code.
The changes below prepare the repository for outside contributors.

- **Removed `docs/AUDIT-2026-09-22.md`.** Why: a 1,600-line working record of
  the phase 1-6 audit. Every finding was fixed in Phase 6.5 and is guarded by a
  maintained test. Section 11 keeps one line per finding. References now say
  the report was removed in Phase 11.
- **Removed `docs/COMPLETION-QUESTIONS.md`.** Why: the agent build plan for
  Phase 8, which is complete. It also held counts taken from the developer's own
  logs. Open items live in `DEFERRED.md`, which now frames them as contributions
  waiting on data.
- **Replaced the real API message and request ids** in the section 2.2 JSONL
  example with placeholders.
- The source archive now has an explicit `only-include` list. Why: Hatch honours
  only the root `.gitignore`, so a local `uv build` packed two stale 0.3.0
  artifacts from an ignored folder. The published 0.4.0 archive was built in CI
  and is not affected.
- Added `SECURITY.md` (private vulnerability reporting), `CODE_OF_CONDUCT.md`
  (Contributor Covenant 2.1), issue forms, a pull request template and a
  Dependabot configuration capped at three grouped PRs per ecosystem.
- README: PyPI, CI and license badges and a fuller contributing section.
- **History rewrite (second, after the 2026-09-22 one).** `git filter-repo`
  removed the two documents above from every commit and replaced the real
  project names (`demo-app`, `DemoLedger`), one real Antigravity conversation id
  and the real message and request ids with placeholders. `master`, this branch
  and tag `v0.4.0` were force-pushed, so every commit id changed. The GitHub
  release and the PyPI files are unchanged; they were built from the commit that
  `v0.4.0` pointed to before the rewrite, whose code is identical. GitHub keeps
  the old commits reachable through pull request refs until GitHub Support
  purges them. A bundle of every ref and a mirror of the old remote were kept
  outside the repository. None of the replaced strings granted access to
  anything.

## 9. Non-goals

Stated so they do not creep in:

- A tray, menu-bar or desktop app. It is a web app. `bozdemir/claude-usage-widget` already does the tray well.
- Reading or storing prompt text beyond the title already on disk.
- Copying code from the reference tools. One is AGPL; borrowing an idea is fine, borrowing source is not.
- Multi-user, auth, or hosting. Single user, localhost.
- Recovering logs already deleted by retention. Impossible; say so.
- Inferring plan limits from past token totals. Utilization comes only from
  official sources (statusline, desktop history, OAuth usage). ccusage removed
  its `blocks --live` monitor after accuracy complaints for exactly this.
- Adopting the Claude Stats / desktop counting rules (raw line sums) for
  headline numbers. They are shown only as a labelled comparison (6.5f).
- An MCP server, for now. ccusage shipped one and removed it (May 2026). A
  stable `--json` CLI output is the preferred agent interface; see Deferred.

---

## 10. Traps

Every one of these produced a wrong number or a broken page during earlier work.

1. Summing JSONL lines instead of deduping responses → 2-3x overcount. **Amended
   2026-09-22:** keeping the *first* line per key undercounts output 7.6-16%,
   because earlier lines carry streaming placeholder `output_tokens`. Keep the
   per-field maximum. Full rule in section 2.2.
2. One flat cache-write multiplier → 38% error on that line.
3. `date.today()` instead of the report timezone → wrong streaks and MTD.
4. Pricing an unknown model at zero → silent undercount. ccusage 20.0.20 does this for current models.
5. Grid children without `min-width: 0` → horizontal page overflow.
6. `cleanupPeriodDays: 0` → no transcripts at all.
7. Recomputing cost for harvested cloud sessions → wrong, no TTL split available. Use the API's `cost_usd`.
8. Treating a single quota reading as a trend → spurious alerts. Needs `quota_samples`.
9. Labelling local-only stats as complete → the "streak is 0 but I use Claude daily" bug.
10. `/btw` aside transcripts re-log parent messages under a new `requestId` → a
    `(message.id, requestId)` key alone double-counts them.
11. Harvesting Remote Control (`bridge`) sessions that also have local transcripts →
    the same work counted twice. Match `bridge-session` ids.
12. Longest-prefix model lookup → a new model id silently priced as an older
    sibling (`claude-opus-4-9` as Opus 4 at $15/$75). Look up exact ids only.
13. Trusting the desktop app's or `/usage` Stats token totals as ground truth →
    about 2.7x overcount (raw line sums, no dedup). See the 2026-09-22 audit report (removed in Phase 11).

Trap 6 is version-dependent: `cleanupPeriodDays: 0` fails validation on Claude
Code 2.1.89 and later (section 2.4).

---

## 11. Deferred

Current actionable inventory and prerequisites: [DEFERRED.md](DEFERRED.md).
The dated entries below are retained as history; resolved items are not active work.

- Codex, Gemini and other agent CLI ingestion (user will implement later).
- PNG export of the dashboard.
- Webhooks and desktop notifications.
- Localization.
- Stored `turns.cost` is computed at sync time. Incremental sync skips unchanged
  files, so a pricing-table or `pricing_overrides` change does not reprice old
  rows. Needs a `vibewatt sync --full` or a reprice pass keyed on the pricing version.
- Resolved in Phase 5: `findings` (listed under "new in phase 1" in section 4)
  is created by the schema v4 migration. This replaces the earlier deferral
  because the analysis engine now persists findings.
- Pre-existing lint debt outside the Phase 1 diff: 23 `ruff check` errors
  (cli.py, doctor.py, dashboard.py, quota.py, sources.py), and `store.py` was
  already unformatted before Phase 1.
- Bug, predates Phase 1 (reproduced on 2ac5456): `vibewatt sessions` with stdout
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
- Phase 2 removed `vibewatt/dashboard.py` (the stdlib `http.server` dashboard).
  Reason: it duplicated the report pipeline FastAPI now serves under `/`,
  `/api/dataset` and `/api/usage` from `vibewatt/api/app.py`, and PLAN.md already
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
- `vibewatt serve` still serves the legacy HTML page at `/`. Serving the React
  build from `vibewatt/static` with an SPA fallback is Phase 7.
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

### Audit status of the items above (2026-09-22)

- Lint entry is stale. The real figures are 21 `ruff check` errors and 15
  unformatted files. `dashboard.py` no longer exists and 4 of the unformatted
  files were introduced in Phases 2-3. Cleared in Phase 7 (A-107).
- The `vibewatt sessions` cp1252 crash is wider than described: the default
  report crashes on any redirected stdout (`terminal.py:68` prints a
  non-cp1252 glyph). Fixed in 6.5c (A-057): redirected streams are UTF-8.
- `date.today()` in `aggregate.py:161,177`, `ui.py:77` and `doctor.py:45` is
  still present. The `aggregate.py` fallbacks are unreachable from `build()`,
  which sets `report.today` from the report tz. `terminal.py:62` is reachable
  and wrong (A-055, 6.5c). `ui.py` is removed in Phase 7.
- The `build_report` quota-per-request item and the "filtered reports skip
  history" item are resolved by 6.5b (store as the only source) and 6.5d
  (throttled quota).
- npm audit: the count of 7 is accurate. The severities were not recorded: 1
  critical and 1 high, both in dev dependencies, plus react-router moderates.
  The Phase 7 toolchain upgrade resolves them (A-118, A-127).
- The `--refresh` serve flag, left behind when `dashboard.py` was removed, is
  still documented. Removed in 6.5e (A-078).
- The repricing item: key the reprice pass on a `PRICING_VERSION` constant
  stamped per stored turn, so a table change reprices automatically.
- Features 7.1 and 7.2 were never assigned a phase. The 7.2 `summary`
  preference cannot be implemented as written, because Claude Code no longer
  writes `summary` records. The title rule moves to 6.5c (A-059, A-128).

### Researched, not scheduled (2026-09-22)

Evidence and sources are in the 2026-09-22 audit report (removed in Phase 11) and the research notes.
The user chose to schedule only the stats reconciliation from this list.

- **Pricing completeness.** These change numbers for some users:
  - Advisor-model tokens live only in `usage.iterations[]` and are dropped
    today.
  - Historical Opus 4.6/4.7 fast mode at $30/$150 (Feb-Jul 2026) is priced at
    standard, a 6x undercount.
  - The pre-2026 long-context premium (over 200k input on Sonnet 4/4.5 until
    2026-04-30, on Opus/Sonnet 4.6 until 2026-03-13) is not applied.
  - `web_fetch` and code-execution counts are not recorded.
  - Rows are missing for `claude-mythos-preview` ($25/$125) and retired
    Sonnet 3.5/3.7.
- **Drain explainer / metering drift.** Quota % gained per $100
  API-equivalent over time and per Claude Code version, the 1h/5m cache-write
  share and limit-regime annotations. The most-reacted usage complaints in
  anthropics/claude-code (#16157, #38335) ask for this.
- **Multi-account and multi-machine.** Several `CLAUDE_CONFIG_DIR` roots
  tagged by account, org-keyed quota and export/import to merge another
  machine's store. No daemon, no network.
- **Optional OTLP receiver** for Claude Code's OpenTelemetry `api_request`
  events, for attribution only. JSONL stays the source of truth for tokens.
- **`history.jsonl` activity backfill.** It survives retention. Use
  timestamps and project only, never prompt text.
- **Cloud session listing.** Spike `GET /v1/code/sessions` (claude.ai OAuth,
  undocumented) behind an opt-in flag. `--file` harvest stays the supported
  path.
- **Stable agent-facing JSON.** `vibewatt status --json` and `quota --json`
  with a `schema_version` and exit codes, instead of an MCP server.
- Provenance badges (`official | computed_local | cloud_reported |
  estimate`) and an `as_of` time on every figure.
- Outcome features seen in codeburn: spend linked to commits and a
  before/after follow-up for each dismissed finding.
- Frontend majors: React 19, React Router 8, TanStack Table 9 (an API
  rewrite). Not needed for packaging.
- Browser notifications and a generic webhook for alerts. This extends the
  existing "Webhooks and desktop notifications" item.

### Audit 2026-09-22 findings

One line per finding: ID, severity and the phase that fixes it. Evidence,
repro and suggested fix are in the 2026-09-22 audit report (removed in Phase 11).

- A-001 [critical, 6.5b] history.json rollup permanently loses usage when a day is partially pruned (session spanning midnight, resumed/forked files)
- A-002 [high, 6.5b] Restored history days show unknown-model usage as $0 and drop the unpriced flag
- A-003 [high, 6.5b] Dedup keeps the first (streaming placeholder) usage line, undercounting output tokens by 7.6% on real data
- A-004 [high, 6.5e] CSRF: POST /api/harvest accepts a cross-origin text/plain body and writes fake sessions and costs to the store
- A-005 [high, 6.5e] Section 5 endpoints only partly built: POST /api/sync does not stream, /api/health lacks last-sync and coverage gaps, /api/quota lacks recent samples
- A-006 [high, 6.5d] Burn alert effectively never fires under the app's own quota sampling (burst samples give zero slope)
- A-007 [high, 6.5e] No Host-header validation: DNS rebinding lets any website read sessions, export, concierge and trigger paid calls
- A-008 [medium, 6.5b] Timezone change double-counts turns in the history rollup
- A-009 [medium, 6.5b] Session/project attribution of a replayed response depends on sync order (store vs live report disagree)
- A-010 [medium, 6.5b] Records lacking message.id: live report and store count them differently
- A-011 [medium, 6.5c] Unknown model ids silently inherit an older sibling's rate via prefix match; the remote table cannot fill the gap
- A-012 [medium, 6.5c] normalize() misses Bedrock global./jp./au./us-gov. inference-profile prefixes, so those models are unpriced
- A-013 [medium, 6.5d] Stale statusline dump is recorded as a 0% quota sample at its original capture time
- A-014 [medium, 6.5c] Pricing non-negotiables have no direct tests: cache-write TTL split, cache-read rate and unknown-model-as-unpriced in cost_of all survive mutation
- A-015 [medium, 6.5c] 'Built-in pricing table outranks fetched table' is untested
- A-016 [medium, 6.5c] Report 'today' from the report timezone is untested
- A-017 [medium, 6.5c] Cost inputs geo, fast mode and web_search are untested through the parser and cost_of
- A-018 [medium, 6.5b] Incremental sync (mtime, size) check: the size half is untested
- A-019 [medium, 6.5c] Streak yesterday-grace and month-to-date year check are untested
- A-020 [medium, 6.5c] Pricing lookup: longest-prefix match and cloud-provider prefix stripping are untested
- A-021 [medium, 6.5b] include_sidechains=False is untested
- A-022 [medium, 6.5b] History restore 'live logs win' precedence is untested
- A-023 [medium, 6.5c] One wrong-typed field in any log crashes sync (rolling back every file) and returns 500 on every report endpoint
- A-024 [medium, 6.5c] A file that cannot be opened is recorded as parsed, so its turns are skipped for good once the lock clears
- A-025 [medium, 6.5b] Report endpoints re-parse every log file (and re-read quota) on each request: 0.7-3.5 s on real data, 12 s per endpoint and 46 s per Overview load at 1M lines
- A-026 [medium, 6.5c] Unpriced models are exported with cost 0 per row (CSV, JSON export, BucketOut) and no per-row unpriced flag
- A-027 [medium, 6.5e] Harvested session with usage but no cost_usd is stored as $0 and not flagged unpriced (sessions, Wrapped, API-equivalent multiple)
- A-028 [medium, 6.5e] Extreme valid dates overflow in store date boundaries: /api/sessions (from=0001-01-01 or to=9999-12-31) and /api/wrapped?year=1 return 500 east of UTC
- A-029 [medium, 6.5e] Project filter cannot match names returned when project_aliases or mask_projects is set
- A-030 [medium, 6.5e] mask_projects is ignored by /api/sessions, /api/session-facets and /api/sessions/{id}, so raw project names leak
- A-031 [medium, 6.5e] POST /api/harvest and `vibewatt harvest` crash or silently accept malformed input
- A-032 [medium, 6.5e] Harvest entries without an id are stored with a NULL key: re-harvest doubles them and /api/sessions returns 500
- A-033 [medium, 6.5e] OpenAPI is incomplete: several responses untyped and the harvest request body undocumented
- A-034 [medium, 6.5c] Report-timezone day/hour bucketing and date-range filters in the report pipeline are untested
- A-035 [medium, 6.5e] Model filter is unverified on most endpoints and in the web client
- A-036 [medium, 6.5d] Account-wide plan utilization meters disappear when filters match no local usage (and while summary loads or fails)
- A-037 [medium, 6.5g] Overview 'Daily cost' bar chart silently drops the most recent days once the range exceeds 240 days
- A-038 [medium, 6.5g] Sticky filter bar hides the focused element when tabbing backwards
- A-039 [medium, 6.5b] PLAN 7.1 Verify (session total equals sum of its turns) has no real test
- A-040 [medium, 6.5g] Heatmap makes every day a tab stop (420 of 457) and there is no skip link
- A-041 [medium, 6.5f] Anomaly baseline zero-fills inactive days, so users active 3 or fewer days a week get every active day flagged, as does every day back from a break
- A-042 [medium, 6.5f] No floor when MAD = 0: any day above the median is flagged, including float summation noise
- A-043 [medium, 6.5f] 'Not enough history' note is wrong for ranges without activity and hides the real cause when an unpriced day lies outside the range
- A-044 [medium, 6.5b] v4 migration could destroy user tables without any test failing
- A-045 [medium, 6.5f] Anomaly 'median + 3xMAD, not mean + stdev' is not verified
- A-046 [medium, 6.5f] Finding-detail modal from the section 6 spec is not implemented and no deviation recorded
- A-047 [medium, 6.5e] Body-less POSTs (/api/sync, /api/analysis, /api/weekly-summary) run on cross-origin simple requests; weekly-summary spends API credit
- A-048 [medium, 6.5d] Two-sample slope on close, whole-percent samples produces spurious burn alerts
- A-049 [medium, 6.5f] Wrapped top_projects does not merge projects that share an alias
- A-050 [medium, 6.5d] Spike alerts are not time-scoped: every historical spike is an active alert, rescanned with per-turn median on each GET (17,436 alerts, 4.95 MB, 3.65 s at scale)
- A-051 [medium, 6.5f] Wrapped year input commits every keystroke: the first digit unmounts the input and triggers full-log refetches
- A-052 [medium, 6.5f] Wrapped streak, daily and hourly bucketing in the report timezone is untested
- A-053 [medium, 6.5d] Burn alert: a sub-100% projection is never tested
- A-054 [medium, 7] Legacy dashboard served at / embeds data unescaped: </script> breakout and innerHTML sinks for project/model names
- A-055 [medium, 6.5c] CLI heatmap anchors on system date.today() instead of the report timezone's today
- A-056 [medium, 6.5e] GETs that persist findings (/api/findings, /api/alerts), dismiss and a second sync return 500 'database is locked' while a sync holds the write lock
- A-057 [medium, 6.5c] Windows cp1252 stdout crash is broader than Deferred says: default `vibewatt`/`vibewatt report` fails on any data when stdout is redirected
- A-058 [medium, 6.5c] IANA timezones such as the README's `Asia/Tokyo` fail on Windows because tzdata is not a dependency
- A-059 [medium, 6.5c] Session titles ignore `summary` records, though PLAN 7.2 says to prefer them
- A-060 [medium, 6.5c] PLAN 7.2 Verify has no suite test for the first-user-message fallback
- A-061 [medium, 6.5e] README Privacy section is false: prompt text is stored and more than one network call exists
- A-062 [medium, 6.5g] Muted text token --tm fails WCAG AA contrast in the light theme but carries local-only labels
- A-063 [low, 6.5c] Flat cache-write fallback silently assumes 5m TTL (undocumented) and a partial split drops tokens
- A-064 [low, 6.5b] cloud ingest module does not implement the common discover()/parse(path) interface; deviation unrecorded
- A-065 [low, 6.5c] Without cwd, project attribution uses the encoded dir name and nested subagent files get project 'subagents'
- A-066 [low, 6.5c] Remote pricing rates are not validated: zero, negative or NaN costs accepted as real rates
- A-067 [low, 6.5c] Every distinct last-prompt text per session is retained, not just the title
- A-068 [low, 6.5c] Parser/ingest edge rules untested: <synthetic> skip, id-less turns, cloud sessions without usage
- A-069 [low, 6.5c] A UTF-8 BOM makes sync drop the file's first record
- A-070 [low, 6.5c] discover() follows NTFS junction loops: 64 paths for 1 file, each parsed separately
- A-071 [low, 6.5b] First sync holds every changed turn in memory in one transaction: 492 MB peak for 415k responses, with no progress
- A-072 [low, 6.5e] CSV export does not neutralise spreadsheet formula cells
- A-073 [low, 6.5e] Shared filter dependency applied inconsistently: from>to only rejected by findings, wrapped ignores from/to, source/metric/cursor unvalidated
- A-074 [low, 6.5e] /api/blocks lists blocks oldest first with the active one last; spec says active first
- A-075 [low, 6.5e] breakdown/surface is just an alias of breakdown/source
- A-076 [low, 6.5e] test_api.py is too shallow and the gate test never runs `vibewatt json`
- A-077 [low, 6.5f] Derived KPI formulas untested: plan multiple, cache hit rate, block hour anchoring
- A-078 [low, 6.5e] `--refresh` serve flag is dead but still documented
- A-079 [low, 6.5g] Costs below $0.01 render as $0.00 in the hero, KPIs, sessions, blocks and Wrapped
- A-080 [low, 6.5g] FastAPI 422 validation errors render as '422 [object Object]'
- A-081 [low, 6.5g] Footer has no links
- A-082 [low, 6.5g] Impossible date (from=2026-13-45) passes URL parser, blanks the input and shows a full-page error
- A-083 [low, 6.5d] Quota meter aria-valuenow is not clamped to aria-valuemax
- A-084 [low, 6.5g] Error-state heading uses --serious as text colour and falls below AA in both themes
- A-085 [low, 6.5g] Overview has no h1 and document.title never changes per route
- A-086 [low, 6.5g] Some tables are not paginated: Overview daily table and Wrapped tables
- A-087 [low, 6.5g] Footer coverage note says figures include harvested sessions but Overview, Projects and Models are local-only
- A-088 [low, 6.5g] Sessions embedded on the Models drill-down are headed 'Project sessions'
- A-089 [low, 6.5g] Hour-of-day Recharts tooltip text uses the series colour and a hard-coded #ccc cursor in both themes
- A-090 [low, 6.5g] Charts and heat cells lack proper accessible names
- A-091 [low, 6.5f] A dismissal applies only to the exact filter selection, so a dismissed finding returns under a project filter or explicit date range
- A-092 [low, 6.5f] Each findings GET re-runs the full analysis and rewrites the snapshot; findings rows grow without bound
- A-093 [low, 6.5f] 'Not enough history' appears only inside the collapsed Coverage section; the anomaly group reads 'No matching findings.'
- A-094 [low, 6.5f] Waste rule cache_to_output: the 200k bound is untested
- A-095 [low, 6.5e] Status fetch 3 s timeout is per socket read, not total
- A-096 [low, 6.5d] Alert findings.day uses the UTC date, not the report timezone
- A-097 [low, 6.5f] Wrapped snapshot fixture is thin: one month, one model, zero cache savings
- A-098 [low, 6.5f] Wrapped dedup of a harvested cloud twin against an unsynced local session is untested
- A-099 [low, 6.5f] PLAN 7.13 and 7.8 Verify tests are narrower than their clauses
- A-100 [low, 6.5f] Missing empty states: Wrapped tables and zero-window quota response
- A-101 [low, 6.5b] The '2.8x overcount' claim holds for input/output only; corpus-wide token overcount is 2.1x
- A-102 [low, 6.5e] Auto-fetched LiteLLM pricing table (and quota response, harvest body) read with no size cap
- A-103 [low, 6.5e] Only --host 0.0.0.0 warns; other non-loopback binds are silent and the warning understates write access
- A-104 [low, 7] CLAUDE.md commands broken: single-test example references a nonexistent test and `pip install -e ".[dev]"` installs no dev tools
- A-105 [low, 7] FEATURES.md reports 11 implemented features as 'no' and has a stale score
- A-106 [low, 7] PLAN sections 1, 3 and 5 no longer match the code (argparse not Typer, deleted dashboard.py, pages/, missing endpoints); deviations unrecorded
- A-107 [low, 7] Deferred lint/format entry is stale and 4 phase-introduced unformatted files are called pre-existing
- A-108 [low, 6.5e] docs/DATA-SOURCES.md not updated for Phase 5-6 inputs and outbound calls
- A-109 [low, 6.5g] Frontend unit coverage is 3 tests on filter round-trip; formatting, date and error logic untested
- A-110 [low, 6.5g] Hard-coded colours outside the token blocks
- A-111 [low, 6.5g] min-width: 0 missing on several grid children
- A-112 [low, 6.5g] No test covers the theme toggle or tokens; core-view overflow gated in light mode only
- A-113 [info, 6.5c] sync_tz identity includes the current UTC offset, so every DST switch forces a full rebucket and re-parse
- A-114 [info, 6.5b] files.turn_count stores pre-dedup assistant lines, not responses
- A-115 [info, 6.5e] /api/hourly returns a 24-bucket hour-of-day vector, not a matrix (unverified spec reading)
- A-116 [info, 6.5f] Session count semantics differ: vibewatt counts only sessions with at least one kept, deduped, non-synthetic turn
- A-117 [info, 6.5g] FilterBar ignores a failed unfiltered-summary (options) query
- A-118 [info, 7] npm audit severities not recorded in Deferred (1 critical, 1 high in dev deps)
- A-119 [info, 6.5f] Cache saving also converts cache writes to reads (beyond the spec formula); spec fixture still matches
- A-120 [info, 6.5f] Anomaly uses raw (unscaled) MAD: matches spec text but gives 6-10% false positives on stable spend
- A-121 [info, 6.5e] tool_reads.path_hash is an unsalted sha256(session\0path) with the session id in the same row, so guessed paths can be confirmed
- A-122 [info, 6.5f] Peak-window finding says 'report timezone' without naming it
- A-123 [info, 6.5f] Analysis severity and 'Show dismissed' controls live in component state, not the URL
- A-124 [info, 6.5b] Wrapped headline (stored) differs from /api/summary for the same range once transcripts are pruned
- A-125 [info, 6.5f] vibewatt does not match the Claude desktop app's stats: desktop sums every JSONL line with no dedup and its snapshot ends 2026-09-15 19:13 JST
- A-126 [info, 7] Legacy page 'Generated' timestamp is naive system-local time shown next to the report timezone
- A-127 [info, 7] npm audit: react-router 6.30.6 has 2 moderate advisories (not reachable in this SPA)
- A-128 [info, 7] PLAN 7.1 and 7.2 are not assigned to any phase
