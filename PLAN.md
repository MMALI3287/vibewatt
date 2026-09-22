# vibewatt — implementation plan

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
| Local log parsing, Claude Code + Cowork | `vibewatt/sources.py` | done |
| Pricing incl. per-TTL cache, fast mode, geo | `vibewatt/pricing.py` | done |
| Aggregation, 5h blocks, burn rate | `vibewatt/aggregate.py` | done |
| Account-wide plan utilization | `vibewatt/quota.py` | done |
| Cloud session harvest | `vibewatt/store.py` `vibewatt/cli.py` | done |
| SQLite store | `vibewatt/store.py` | done |
| Durable history across log pruning | `vibewatt/history.py` | done |
| Diagnostics | `vibewatt/doctor.py` | done |
| Terminal report | `vibewatt/terminal.py` | done |
| Single-file interactive HTML | `vibewatt/ui.py` | done, to be superseded by the React app |
| Dashboard server | `vibewatt/dashboard.py` | done, to be replaced by FastAPI |

**Audit 2026-09-22:** phases 1-6 were audited against this plan. 128 findings,
evidence in `docs/AUDIT-2026-09-22.md`, one line each in section 11, scheduled
as Phase 6.5a-g below. The table above is partly stale (`dashboard.py` was
removed in Phase 2; the CLI is argparse, not Typer); Phase 7 refreshes it.

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
fix. Recommend 3650. `vibewatt/history.py` and the SQLite store preserve rollups
from first run forward but cannot recover what was already deleted.

**Corrected 2026-09-22 (audit):** since Claude Code 2.1.89 `cleanupPeriodDays: 0`
fails settings validation instead of silently disabling writing; older versions
still have the trap. Minimum is 1, default still 30 and the sweep runs as a
background task after start. Since 2.1.248 desktop and Cowork transcripts are
exempt from age cleanup unless `desktopSessionCleanupPeriodDays` or a managed
`cleanupPeriodDays` is set. The promise above does not hold today: history.json
permanently loses partially pruned days (A-001). Phase 6.5b makes the store the
only source of headline numbers.

---

## 3. Target architecture

```
vibewatt/                     Python package
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
**Scope:** generate a resume brief for a project — last session title, uncommitted changes, unfinished todos — as **text the user copies**. vibewatt does not talk to a running Claude session.
**Verify:** produces a brief for a fixture project; writes nothing outside its own data dir.

### 7.14 Other agent CLIs
**Deferred by the user.** Design `vibewatt/ingest/` so a new provider is one module exposing `discover()` and `parse()`. Do not implement Codex or Gemini now, but do not hard-code "claude" into the schema either — `turns.source` is already free text.

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
remaining correctness gaps. See `docs/PHASE-4.md` for validation and replacements.
**Do:** Sessions (infinite scroll, search, detail modal at `/sessions/:id`), Projects (drill-down + breadcrumbs), Models, heatmap with metric switch, hour-of-day, blocks.
**Gate:** Playwright drives filter changes and asserts the KPI row recomputes; session detail deep-links and the back button closes the modal.

### Phase 5 — Analysis engine
**Status:** implemented and verified on 2026-09-21. Phase Gate: 40 passing
tests. Independent review found no remaining correctness gaps.
**Implementation notes:** see `docs/PHASE-5.md` for rule thresholds, metadata
backfill and the approved local-context limitation.
**Do:** `vibewatt/analysis/` implementing 7.4, 7.6, 7.7, 7.9, 7.10, 7.11. Write to `findings`. Analysis page with accordions per kind, severity badges, dismiss.
**Out of scope:** the AI summary and service status.
**Gate:** `uv run pytest -q tests/test_analysis.py` — every rule has a triggering and a non-triggering fixture.

### Phase 6 — Wrapped, alerts, AI summary, status
**Status:** implemented and verified on 2026-09-21. Phase Gate: 25 passing
tests plus 30 passing browser checks. Independent review found no remaining
correctness gaps. See `docs/PHASE-6.md` for coverage, bounds and validation.
**Do:** 7.3, 7.5, 7.8, 7.12, 7.13.
**Gate:** Wrapped snapshot test; alert fires once not per sample; AI summary makes no network call when disabled; dashboard renders with the status fetch stubbed to fail.

### Phase 6.5: Audit fixes and spec corrections (added 2026-09-22)
**Why:** the phase 1-6 audit (`docs/AUDIT-2026-09-22.md`) found 128 issues:
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
- Kept on purpose: `docs/AUDIT-2026-09-22.md`, `docs/PHASE-4.md` and
  `docs/PHASE-5.md` are dated records; their `ccburn/...` paths are evidence of
  the code as audited, so they were not rewritten. The probes were renamed so
  they run.
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

### Phase 7 — Packaging and polish
**Status:** work in progress exists uncommitted on `feat/phase-7-packaging-polish`
(SPA serving, build hook, packaging tests). Rebase it onto Phase 6.5g and the rename.
**Do:** ship `web/dist` into the wheel as `vibewatt/static`. `vibewatt serve` opens the React app. Windows path tests. Docs. Screenshots in the README.
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
- Docs refresh: section 1 and 3 drift, FEATURES.md, README, CLAUDE.md
  commands and repository-wide lint and format debt (A-104 to A-107, A-128).
**Gate:** `pip install dist/*.whl` in a clean venv on Windows and Linux, then `vibewatt serve` renders the dashboard with no Node present.

---

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
    about 2.7x overcount (raw line sums, no dedup). See `docs/AUDIT-2026-09-22.md`.

Trap 6 is version-dependent: `cleanupPeriodDays: 0` fails validation on Claude
Code 2.1.89 and later (section 2.4).

---

## 11. Deferred

Append here rather than widening a phase.

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
  non-cp1252 glyph). Scheduled in 6.5c (A-057).
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

Evidence and sources are in `docs/AUDIT-2026-09-22.md` and the research notes.
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
repro and suggested fix are in `docs/AUDIT-2026-09-22.md`.

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
