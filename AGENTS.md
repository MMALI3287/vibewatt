# vibewatt: instructions for coding agents

These rules apply to any coding agent (Claude Code, Codex, Cursor and others)
and to people. `CLAUDE.md` imports this file, so there is one copy.

vibewatt reports token usage, cost and plan utilization for Claude Code and
Cowork. A Python backend reads local logs into SQLite; a React frontend renders
it. `docs/PLAN.md` is the spec and the phase plan. `docs/DATA-SOURCES.md` says
where every number comes from. `docs/ANALYSIS.md` explains how findings, alerts
and Wrapped are computed.

Current work is Phase 8 (backlog completion). Its build plan, scope limits and
acceptance checks are in `docs/COMPLETION-QUESTIONS.md`. Start there.

## Commands

```bash
uv sync                                   # install with the dev tools
uv run vibewatt sync                      # parse local logs into SQLite
uv run vibewatt harvest --file s.json     # ingest cloud session usage
uv run vibewatt doctor                    # what the tool can and cannot see
uv run vibewatt serve                     # API + dashboard on :8777

uv run pytest -q                          # tests (fast, no network)
uv run pytest -q tests/test_dedup_store.py::test_placeholder_then_final_keeps_the_final_counts
uv run ruff check . && uv run ruff format --check .

cd web && npm ci                          # frontend dependencies
cd web && npm run dev                     # frontend dev server
cd web && npm run build                   # production build into vibewatt/static
cd web && npm run test                    # vitest
cd web && npx playwright test             # end-to-end, after npm run build
cd web && npm run gen:api                 # after any API change; commit both files
```

## Non-negotiables

These rules produce wrong numbers when broken. Each one cost a real bug.

- **Dedup every response, keeping the per-field maximum.** Claude Code writes one
  JSONL line per content block and repeats the whole-response `usage` on each.
  Summing lines inflates input and output about 2.8x. Keeping the first line
  undercounts output 7.6-24%, because it carries placeholder `output_tokens`.
  Key on `(message.id, requestId)` (Cowork spells it `request_id`). Drop a
  sidechain line whose `message.id` is on a main-thread line. Without a
  `requestId`, key on `(session, message.id, timestamp)`. The result must not
  depend on file order. Code: `sources.dedupe()` and `store.upsert_turns()`.
- **Report numbers come from the store only.** Endpoints and CLI reports read the
  `rollup` table; only `sync` reads logs. Never add a request path that parses
  JSONL.
- **Price cache writes per TTL.** `cache_creation.ephemeral_1h_input_tokens` bills
  at 2x base input; `ephemeral_5m_input_tokens` at 1.25x. Never apply one flat
  multiplier to `cache_creation_input_tokens`.
- **Never price an unknown model at zero.** Report it as unpriced instead.
  `vibewatt/pricing.py` is the only place rates live. Lookup is by exact model
  id. The built-in table outranks any fetched table.
- **Use the report's timezone for "today", never `date.today()`.** Streaks and
  month-to-date break otherwise. Day boundaries also honour `day_start_hour`.
- **Label local-only stats as local-only.** Plan utilization is the only
  account-wide number. Never imply local logs cover web or Cowork remote.
- **Record every removal and its reason, in the same change.** A later reader
  must be able to find why a file, table, flag or dependency is gone. Records
  live in `docs/PLAN.md` under the phase that made the change.

## Code style

- Python: standard library first. A dependency needs a reason in the PR body.
- Type hints on public functions. `from __future__ import annotations` at the top.
- Comments explain *why*, never *what*. Delete a comment that restates the code.
- Frontend: TypeScript strict. Function components. No `any`: use `unknown` and
  narrow.
- Colours are theme tokens from `docs/DESIGN.md`, never literals.

## Testing

- Every bug fix starts with a failing test that reproduces it.
- Fixtures live in `tests/fixtures/`. Never read the developer's real `~/.claude`.
- Tests must not touch the network. Pricing tests use the built-in table.
- The e2e suite gates page overflow at 1440/1024/768/390 px in both themes and
  text contrast (WCAG AA) on every route.

## Repository etiquette

- The default branch is `master`.
- Branch names: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`.
- Conventional commit subjects. The body explains why, not what changed.
- Run `ruff check`, `pytest` and the web checks before opening a PR.
- Local agent configuration (`.claude/`, `.codex/`, `.agents/`) is personal and
  gitignored. Put shared rules in this file instead.

## Gotchas

- `cleanupPeriodDays: 0` in Claude Code settings disables transcript writing on
  older versions and fails validation on newer ones. It never means "keep
  forever". Use 3650.
- CSS grid children default to `min-width: auto`, so a wide table pushes the page
  sideways. Set `min-width: 0` on grid items.
- The cloud session API and `/api/oauth/usage` are undocumented and may change.
  Every call degrades to local-only rather than failing the command.
- The dashboard refuses a non-loopback `Host` and cross-site POSTs. Test clients
  must use a loopback base URL (see `tests/conftest.py`).
