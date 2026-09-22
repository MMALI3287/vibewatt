# vibewatt

Token usage, cost and plan utilization for Claude Code and Cowork. Python backend
reads local logs into SQLite; React frontend renders it. See `PLAN.md` for the
implementation plan and `docs/DATA-SOURCES.md` for where every number comes from.

## Commands

```bash
uv sync                          # or: pip install -e ".[dev]"
uv run vibewatt sync               # parse local logs into SQLite
uv run vibewatt harvest --file s.json   # ingest cloud session usage
uv run vibewatt doctor             # what the tool can and cannot see
uv run vibewatt serve              # API + dashboard on :8777

uv run pytest -q                 # tests (fast, no network)
uv run pytest -q tests/test_pricing.py::test_cache_ttl_split   # single test
uv run ruff check . && uv run ruff format --check .

cd web && npm run dev            # frontend dev server
cd web && npm run build          # production build into vibewatt/static
cd web && npm run test           # vitest
```

## Non-negotiables

These are the rules that produce wrong numbers when broken. Each one cost a real bug.

- **Dedup every response on `(message.id, requestId)`.** Claude Code writes one
  JSONL line per content block and repeats the whole-response `usage` on each.
  Summing lines inflates totals ~2-3x.
- **Price cache writes per TTL.** `cache_creation.ephemeral_1h_input_tokens` bills
  at 2x base input; `ephemeral_5m_input_tokens` at 1.25x. Never apply one flat
  multiplier to `cache_creation_input_tokens`.
- **Never price an unknown model at zero.** Report it as unpriced instead.
  `vibewatt/pricing.py` is the only place rates live, and the built-in table
  outranks any fetched table.
- **Use the report's timezone for "today", never `date.today()`.** Streaks and
  month-to-date break otherwise.
- **Label local-only stats as local-only.** Plan utilization is the only
  account-wide number. Never imply local logs cover web or Cowork remote.

## Code style

- Python: standard library first. A dependency needs a reason in the PR body.
- Type hints on public functions. `from __future__ import annotations` at the top.
- Comments explain *why*, never *what*. Delete a comment that restates the code.
- Frontend: TypeScript strict. Function components. No class components.
- No `any` in TypeScript. Use `unknown` and narrow.

## Testing

- Every bug fix starts with a failing test that reproduces it.
- Fixtures live in `tests/fixtures/`. Never read the developer's real `~/.claude`.
- Tests must not touch the network. Pricing tests use the built-in table.

## Repository etiquette

- Default branch is `master`. Never push to `main`.
- Branch names: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`.
- Conventional commit subjects. Body explains why, not what changed.
- Run `ruff check` and `pytest` before committing.

## Gotchas

- `cleanupPeriodDays: 0` in Claude Code settings disables transcript writing
  entirely. It does not mean "keep forever". Use 3650.
- CSS grid children default to `min-width: auto`, so a wide table pushes the page
  sideways. Set `min-width: 0` on grid items.
- The cloud session API is undocumented and may change. Every call degrades to
  local-only rather than failing the command.
