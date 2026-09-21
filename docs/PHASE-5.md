# Phase 5: analysis engine

## Delivered

`ccburn/analysis/` evaluates stored, deduplicated local turns, Read tool metadata,
harvested context snapshots and quota samples. It makes no network calls and does
not discover or scan logs during analysis. The Analysis page replaces its route's
placeholder with collapsed finding groups, severity badges, ranked advice,
session links, dismiss/restore controls and loading/error/empty states. Metadata-only
findings remain visible without a session link when no session-detail record exists.

The migration from schema 3 to 4 adds `findings`, `tool_reads` and
`tool_read_files`. Existing usage, prompt, cloud and file-checkpoint rows remain.
Resolved findings become inactive rather than being deleted. Re-running analysis
upserts stable IDs and preserves creation time and dismissal state.

## Interfaces and filtering

- `GET /api/findings` evaluates the current stored snapshot and persists the
  results. It accepts the shared date/source/project/model filters plus `kind`,
  `severity` and `include_dismissed`. It returns typed findings and coverage notes.
- `POST /api/analysis` explicitly refreshes the same snapshot with the same inputs.
- `POST /api/findings/{id}/dismiss` takes `{ "dismissed": true }`. Passing false
  restores a finding. Unknown IDs return 404.
- The Python `analyze(conn, tz, ..., now=...)` entry point accepts an explicit
  clock for deterministic fixtures and returns the same response shape.

Session rules use only matching turns/read events. Anomalies retain the preceding
history for the selected source/project/model even when the displayed date range
is narrower. Date boundaries and today's cutoff use the report timezone.
Harvested context uses the session start date, matching the session API.
Quota patterns apply to account-wide date ranges; source/project/model filters
disable them because quota samples cannot be attributed to those dimensions.

Finding identities include the date/source/project/model selection. Dismissals
persist within that selection across reruns and temporary resolution. Changing
the metric or kind/severity visibility does not reset a dismissal. Different
evidence selections intentionally have separate dismissals.

## Rules and assumptions

| Rule | Trigger and evidence |
|---|---|
| Daily anomaly | Cost greater than trailing calendar-day median + 3 × MAD; exclude the evaluated day, use up to 28 days and require at least 14 preceding days. Fill inactive dates inside the observed span with zero. Exclude any candidate/baseline containing unpriced responses. Local logs only. |
| Cache opportunity | More than 200k total input tokens including cache writes/reads and a cache-hit ratio below 50%. Estimate replacing uncached input and each TTL's cache writes with reads using each response's model, fast-mode and geography rates. Unknown rates yield unavailable savings. |
| Cache advice | Derived from a cache opportunity. Suggest stable instructions and checking cache configuration. |
| Model advice | At least five Opus responses averaging fewer than 500 output tokens. Suggest evaluating Sonnet with quality comparisons; never claim equivalence from output length. |
| Subagent overhead/advice | At least five responses and at least 50% of total tokens attributed to sidechains. |
| Fast-mode advice | Positive fast-versus-standard price difference for the same tokens, or unknown pricing. Preserve per-TTL and geographic pricing. |
| Repeated reads | At least three distinct Read tool invocations for one normalized path in a session. Replayed tool IDs count once. Reads may cover different ranges or changed files, so this is an investigation signal. |
| Cache-read/output waste | At least 200k cache-read tokens and more than 100 cache-read tokens per output token. Zero output is handled without division. |
| Long/low-output session | At least five responses spanning two hours or more with fewer than 1,000 output tokens. Elapsed time includes idle gaps. |
| Peak window | At least two distinct reset windows reached 100% on different dates at the same weekday/hour in the report timezone. Count the first observed limit encounter per reset window before date filtering. Samples without a valid future reset cannot establish an encounter. |
| Context nudge | Latest harvested `context_used/context_max` is above 70% (warning) or above 85% (urgent). Invalid/missing capacities yield no percentage. |

Savings are upper-bound scenarios, not guarantees. Cache-write estimates retain
the 5-minute/1-hour distinction. Suggestions rank by severity and estimated
saving where available. Estimates can overlap and must not be added together.
Cloud costs are never recalculated or changed.

## Repeated-read metadata and privacy

Sync parses only `Read` tool-use IDs, session/time/source/project/model metadata
and a session-scoped hash of the normalized Read target path. `tool_reads` stores
no literal target paths, tool arguments, file contents or prompt contents.
`tool_read_files` retains transcript paths for incremental sync checkpoints,
just as the existing `files` table does.
Paths are normalized using the producer's Windows or POSIX syntax independently
of the host OS. Separate tool-use deduplication is necessary because multiple
tools can share one response's repeated usage record.

The separate `tool_read_files` checkpoint starts empty on upgrade. The next sync
backfills metadata for retained unchanged logs once while the existing `files`
checkpoint continues to skip usage parsing. Later syncs skip unchanged metadata.
No cache reset or deletion is needed. Pruned transcripts cannot be backfilled.
An end-to-end fixture cycle verifies this behavior with replayed content and a
duplicate transcript file. Three reads and two billable responses remain three
reads and two responses after resync.

## Approved deviation and replacements

- Local context percentage is unavailable. The existing block represents usage
  across a five-hour rate-limit period and has no context capacity. Dividing its
  accumulated tokens by a guessed window would invent a percentage. Harvested
  context warnings are implemented; local context remains explicitly unavailable.
- Replaced the Analysis route's placeholder because Phase 5 now supplies the
  implemented view. The shared Placeholder component remains for Wrapped and
  unknown routes. No services, dependencies, source files or stored records were
  removed. No new dependency was added.
- The Phase 1 deferred note about creating `findings` is now resolved by v4.
  AI summaries, burn/spike alerts, service status and Wrapped remain Phase 6.

## Validation

All fixtures use temporary data/config locations and do not read real Claude logs
or the production database. Browser fixtures preserve previous phase usage totals
while adding repeated-read evidence and one high-context harvested snapshot.

Final Phase 5 gate, independently rerun by the plan reviewer:

```text
uv run pytest -q tests/test_analysis.py
40 passed in 0.37s
```

Additional command output:

```text
uv run pytest -q
89 passed, 2 warnings in 2.79s

npm run build
✓ built in 3.56s

npm run test
Test Files  1 passed (1)
     Tests  3 passed (3)

npm run e2e
23 passed (13.3s)

Changed Python files: uv run ruff check <changed paths>
All checks passed!

New analysis code and tests: uv run ruff format --check <paths>
12 files already formatted

uv run ruff check .
Found 21 errors.

uv run ruff format --check .
15 files would be reformatted, 38 files already formatted

Sync idempotence: PASS; responses/cost/tokens = (2, 0.00039999999999999996, 40)
Doctor fixture smoke: PASS; exit=0; coverage reported; quota disabled
```

The full suite's two warnings are existing FastAPI/Starlette test-client
deprecations. Browser checks cover the prior core views plus analysis filtering,
deep links, refresh, dismissal persistence/restoration, empty/loading/error states
and expanded evidence at 1440/1024/768/390px in both themes. The final Python-only
session-link guard was verified by its reproduced failing regression and the full
89-test suite after the browser pass.

Independent plan review found no remaining correctness gaps. Follow-up review
verified the metadata-only link fix and requested the checkpoint-path privacy
clarification now recorded above.

Repository-wide Ruff reports the previously documented 21 errors and formatting
debt in 15 existing files. Changed-file lint passes. The production build retains
the existing bundle-size warning. React production serving remains Phase 7.
