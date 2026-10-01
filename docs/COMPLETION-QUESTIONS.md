# Phase 8 handoff: backlog completion

Decided on 2026-09-26. This file used to hold open questions. It is now the
build plan for the last backlog phase. Every active item in `DEFERRED.md` is
either built in a work package below, moved to a later phase or closed with a
recorded reason. Nothing stays open as "later".

**Start here if you are the implementing agent (Codex or any other).** Read
`AGENTS.md`, then this file, then `docs/PLAN.md` section "Phase 8". Branch from
an up-to-date `master` and build packages 1-5 in order. Stop after package 5 and
hand back. Do not start Phases 9-11.

## Phase map

| Phase | Owner | Content |
|---|---|---|
| 8. Backlog completion | Implementing agent | Packages 1-5 below |
| 9. Verification | Claude Code | Independent audit of Phase 8 against this file and `AGENTS.md`. Fix findings test-first |
| 10. More providers | Claude Code | ChatGPT/Codex CLI usage and Gemini CLI/Antigravity usage |
| 11. Public launch | User and Claude Code | Repository goes public for contributions, then a tagged GitHub release and a PyPI publish |

Phases 10 and 11 are recorded here so Phase 8 does not build them early. The
Codex provider package and the release steps were in the first draft of this
plan. They moved to Phases 10 and 11.

## Decisions

| Topic | Decision | Decided by |
|---|---|---|
| Release | Verified clean-wheel build, a GitHub tagged release, then a PyPI publish of `vibewatt`. No npm reservation. Publishing happens in Phase 11, not Phase 8 | User |
| Platforms | Windows, Linux and macOS. Add `macos-latest` to the CI matrix (package 5) | User |
| Multi-account/machine | Build it (package 3) | User |
| Cloud session listing | Close. File harvest stays supported | User |
| Localization | Close. English only | User |
| Commit spend links | Close | User |
| Notifications | Browser notifications only, opt-in, warnings and above. No webhooks | User |
| Providers | ChatGPT/Codex and Gemini/Antigravity in Phase 10 | User |
| Everything else | As listed below | Agent, from the evidence below |

## Evidence from the developer's local logs (2026-09-26)

Counts come from a read-only grep. No content was copied into the repository.

- `usage.iterations[]`: 18,049 entries. Every entry has `type: "message"`. No advisor iterations exist.
- `speed`: 18,050 lines. Every line is `"standard"`. No fast-mode responses exist.
- `server_tool_use`: 18,174 lines. `web_search_requests` and `web_fetch_requests` are always 0. `code_execution_requests` appears only inside quoted documentation text.
- `~/.codex/sessions`: 95 real Codex session files (used in Phase 10). `~/.gemini` does not exist.
- `~/.claude/history.jsonl`: 145 lines.
- The code has no stray `TODO` or `FIXME` markers. `DEFERRED.md` is the complete backlog.

Consequence: the pricing work has to be built from official documentation and
synthetic fixtures. Real data can only confirm that standard rows stay unchanged.

## What already exists (do not rebuild)

- `turns.fast` stores `speed == "fast"`. `pricing.FAST_MODE` holds current fast rates without effective dates.
- `turns.web_search` stores `web_search_requests`. `pricing.WEB_SEARCH_PER_CALL` prices it.
- `turns.geo` and `GEO_US_MULTIPLIER` handle US inference pricing.
- There is no `reprice` command. `store.reprice()` runs inside every `vibewatt sync` (and `serve`) and reprices retained turns whenever the pricing fingerprint changes. A stale or unreachable remote table falls back to the last cached one, so going offline does not reprice history.
- `claude-mythos-5` and `claude-mythos-5-1` are priced. A separate Mythos preview id is not.

## Rules for every package

- Branch from current `master`: one branch and one PR per package (`feat/<slug>`), merged in order.
- Every behavior change starts with a failing test. Fixtures go in `tests/fixtures/`, never the real `~/.claude`, `~/.codex` or `~/.gemini`.
- Every rate cites its official source URL and retrieval date in a comment in `vibewatt/pricing.py`. A rate that cannot be verified stays unpriced. It is never guessed or priced at zero.
- Back up the store before any schema migration. After any pricing change, run `vibewatt sync` against a copy of a real store (point `VIBEWATT_DATA_DIR` at the copy) and put the before/after totals from `vibewatt json` in the PR body.
- A new collector gets a byte and record bound before it is enabled, plus one observed real cycle showing that the bound applies.
- When a package finishes an item, remove that row from `DEFERRED.md` and add the removal record under "Phase 8" in `docs/PLAN.md` in the same PR.
- Gate for every PR, the same as CI: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q`, `npm run test`, `npm run build`, `npm run gen:api` with no diff, `npm audit --omit=dev --audit-level=high` and `npm run e2e`.

## Work packages

### 1. Pricing completeness (`feat/pricing-completeness`)

1. **Missing model rates.** Check the official Anthropic model and pricing pages. Add exact ids and rates for every Anthropic model seen in retained data, including retired Sonnet 3.5 and 3.7. Add the Mythos preview id only if an official rate is published. Otherwise it stays unpriced and the gap is recorded in `DATA-SOURCES.md`.
   - Accept: a fixture turn for each new id moves from unpriced to priced through `store.reprice()`. Unknown ids still report as unpriced.
2. **Historical fast-mode rates.** Give `FAST_MODE` effective periods (start and end date per model rate) from official dated sources, covering Opus 4.6 and 4.7. Price each fast turn by its timestamp. A fast turn with no verified rate for its date is unpriced, never priced at the standard rate. No schema change is needed.
   - Accept: fixtures on each side of every effective date. Repricing a real store changes nothing, since every real row is standard.
3. **Long-context premium.** The per-response prompt size is `input + cache_read + cache_creation` of that response. That is per-response evidence, never a session total. Above the verified threshold on a verified model and date, apply the verified premium to that response only.
   - Accept: fixtures at threshold - 1, threshold and threshold + 1 for each affected model. Rows on models with no verified threshold carry a `context_premium_unknown` flag instead of a guess.
4. **Web fetch and code execution.** Add `web_fetch` and `code_execution` counts per deduped response (schema migration, default 0, per-field maximum in dedup like the other counters). Web fetch adds tokens only; confirm in the official docs that it has no per-request fee. Code execution is billed by container time, which the logs do not contain. Show its count and mark its cost "unavailable", which is different from $0.
   - Accept: counts survive dedup. The dashboard shows the counts and labels unavailable cost as unavailable.
5. **Advisor guard.** Any `usage.iterations[]` entry whose `type` is not `"message"` increments a `nonstandard_iterations` counter that `vibewatt doctor` reports. It is not priced or added to totals. Advisor pricing itself is closed (see Closed items).
   - Accept: a synthetic fixture with an advisor-type iteration raises the doctor warning and leaves totals unchanged.

### 2. Context nudges and activity backfill (`feat/context-and-activity`)

1. **Local context nudges.** For each session active in the last 30 minutes, current context is the prompt size of its latest main-thread response (same formula as 1.3). The maximum is the model's verified standard window. Use 1M only if the model id says so or that session already had a response above the standard window. No statusline capture. Rate-limit blocks are never read as context.
   - Accept: a fixture proves a quota block does not change the nudge. A session idle for more than 30 minutes shows no nudge. Thresholds reuse the existing cloud snapshot ones.
2. **`history.jsonl` activity backfill.** Read only `timestamp` and `project`. Drop `display`, `pastedContents` and every other field at parse time. Bound: 50 MB and 500,000 records per sync; over-cap input is truncated with a doctor warning. Dedup on `(timestamp, project)`. The output feeds a separately labelled "activity" calendar and streak. It adds no tokens or cost.
   - Accept: a fixture containing prompt text proves no prompt text reaches the store (the test greps the SQLite file). One real sync of the developer's 145-line file is observed and its count recorded in the PR.

### 3. Multi-account and multi-machine (`feat/accounts-machines`)

- **Identity.** Account = the OAuth account UUID from Claude Code's local config when present, otherwise `"unknown"`. Machine = a random UUID stored once in the vibewatt config directory. Store both on every turn (migration with backup; existing rows get this machine's id).
- **Export.** `vibewatt export --out file.vwx`: versioned gzip JSON of turns and rollup inputs only. No titles unless `--include-titles`. Hard cap of 200 MB.
- **Import.** `vibewatt import file.vwx`: rejects a wrong schema version or over-cap input before any write. Dedup uses the existing turn key plus machine id. Conflicts keep the per-field maximum, the same rule as dedup. Import is idempotent.
- **Scope.** Reports default to all machines of the selected account. Accounts never merge. Plan utilization stays scoped to the account it was fetched for.
- Accept: importing the same file twice changes nothing. Two isolated fixture stores merge to the expected totals. A different-account import stays separate. Oversize or bad-version input is rejected with the store untouched.

### 4. Dashboard export and notifications (`feat/export-notify`)

1. **PNG export.** Exports the selected view with its filters, theme and provenance footer. Project names are masked by default. A checkbox includes them. Prefer serializing the existing SVG charts with no new dependency. If a library is unavoidable, the PR body says why.
   - Accept: Playwright exports each route at 1440 and 390 px in both themes, checks that nothing is clipped and checks that a masked export contains no real project name.
2. **Browser notifications.** Off by default. Turning them on asks for browser permission. They fire only for alerts of severity warning or higher, while a dashboard tab is open (no service worker, no push server). Dedup by alert id, with a 6-hour cooldown per alert kind.
   - Accept: unit tests for dedup and cooldown. One real notification is observed on the developer's machine. A repeat within the cooldown is shown to be suppressed.

### 5. Platform coverage and release readiness (`chore/phase-8-close`)

1. Add `macos-latest` to `.github/workflows/ci.yml`. Fix whatever it finds, test first.
2. Confirm `DEFERRED.md` has no active Phase 8 rows left and `PLAN.md` "Phase 8" has a removal record for each.
3. Build the wheel and clean-install it in a fresh venv on Windows, Linux and macOS (CI is acceptable for Linux and macOS). Check that `vibewatt serve` works without Node and that a 0.3.0 store is preserved and migrated.
4. Do not bump the version, tag, create a GitHub release or publish to PyPI. That is Phase 11.

## Closed items

These are recorded with the same reasons in `docs/PLAN.md` under "Phase 8".

| Item | Reason | Reopen trigger |
|---|---|---|
| Advisor-model pricing | No advisor iterations exist in 18,049 real entries, so overlap and rates cannot be verified. Package 1.5 adds a detection guard instead | The doctor warning fires on real data |
| Opt-in cloud session listing | User decision. The endpoint is undocumented and needs credentials. File harvest covers the need | A documented endpoint exists |
| OTLP receiver | JSONL already supplies every attribution field the reports use. A receiver adds a network surface for no current gain | A needed field exists only in OTLP |
| Localization | User decision: English-only release | A named language with a terminology reviewer |
| Drain explainer and metering drift | Needs months of quota history with version provenance. Building it on thin history would imply plan limits the data cannot support. Plan meters already show quota | 90 or more days of retained quota history |
| Commit spend links and finding outcomes | User decision on commit links. Finding outcomes were the same backlog item and share its causation risk, so they close with it | The user asks for either |
| React 19, Router 8, TanStack Table 9 | No concrete compatibility or security need. The CI `npm audit` gate covers advisories | An advisory, end of life of a current major or a blocked dependency |
| npm name reservation | Not needed for a Python wheel | A JavaScript package is planned |

## Phase 8 acceptance

Completed 2026-10-01. Packages 1-4 are PRs #13-#16. The close-out PR must pass
the full Windows, Linux and macOS matrix at its exact head before it is merged;
the checked acceptance below describes the resulting master state.

- [x] Packages 1-4 merged with green CI.
- [x] Package 5 merged with green CI on Windows, Linux and macOS.
- [x] Each new collector (history backfill, import) has its observed real cycle recorded in its PR.
- [x] `DEFERRED.md` lists only Phase 10 items. Every Phase 8 item has a removal record in `PLAN.md`.
- [x] A clean wheel install serves the dashboard without Node and preserves 0.3.0 data on all three platforms.
- [x] Nothing was tagged or published.

Evidence: PR #14 observed 145 activity rows and a zero-row repeat with a reduced
record cap exercised. PR #15 observed 8,338 transferred responses and unchanged
$2,368.99880125 cost, a zero-row repeat and rejection at a reduced 10-record cap.
PR #16 verified 24 PNG route/width/theme combinations and native Edge delivery
with repeats suppressed. The close-out CI runs the synthetic schema 9 upgrade,
backup verification and installed-wheel browser cycle on all three platforms.
Local final checks passed 353 Python, 35 frontend unit and 63 browser tests.

Phase 8 stops here. Phase 9 independently audits these claims. Providers and
public publishing remain in Phases 10 and 11.
