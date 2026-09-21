# Phase 6: Wrapped, alerts, weekly summary, status and resume briefs

## Delivered

- `/api/wrapped?year=` and the Wrapped route show a calendar-year review, top
  projects, monthly model mix, busiest day/hour, longest observed streak,
  biggest session by tokens, cache-read savings and a downloadable SVG share
  card. Source/project/model filters apply. The selected year replaces date
  filters and survives filter changes and reloads.
- `/api/alerts` evaluates account-wide quota projections and local response-cost
  spikes. Findings store persistent identities so each reset window or response
  fires once across refreshes and restarts. Concurrent requests serialize the
  identity check. Existing findings are retained rather than deleted.
- `POST /api/weekly-summary` generates a last-seven-calendar-days summary only
  when explicitly requested and enabled. The disabled path does no report work
  and makes no network request.
- `/api/status` independently fetches public Anthropic status. The panel is
  absent while loading or when the request fails. Offline configuration skips
  fetching. Success and failure both cache for five minutes.
- `/api/concierge?project=` returns copyable text with the latest session title,
  Git status and unfinished checkboxes from a bounded `TODO.md` read. It never
  writes to the project or sends text to a running session.

## Coverage and interpretation

The existing `/api/summary` reparses live local logs. It does not include
retained store-only turns or harvested sessions. Wrapped exposes
`local_summary` through that same pipeline so its totals match the summary for
the same year and facets. Separate `stored_*` figures cover retained local turns
plus harvested sessions without overlapping local IDs. Sync populates retained
local data. This preserves the existing summary contract instead of silently
making its coverage broader in this phase.

Stored costs retain sync-time pricing. Harvested API costs are never recalculated.
Unpriced local responses are explicitly counted and exclude the API-equivalent
multiple. Cache savings are local cache-read tokens valued at the model's input
minus cache-read rate with speed/geography overrides; unknown rates return
unavailable. They do not estimate cloud savings or subtract cache-write costs.

Day and year boundaries use the report timezone. Busiest day and biggest session
mean highest token count. Cloud totals belong to their session start day; no
per-response cloud hour can be inferred. Hour-of-day figures cover stored local
turns only. The longest streak is an observed activity streak within the year.
The plan multiple compares API-equivalent cost with twelve times the configured
monthly price, not actual historical payments. The SVG card contains aggregate
figures and coverage notes without project names or session titles.

Burn projections require two valid, rising, chronologically distinct quota
samples in the same unexpired reset window. One reading cannot establish a
trend. A spike exceeds five times the previous fifty priced responses' median;
the candidate is excluded from that baseline. Unknown costs do not enter it.
The active block reports local burn context only, never a fabricated conversion
from tokens to account-wide utilization. Quota samples are collected by the
existing quota reads. Alerts use stored samples and do not fetch quota themselves.
Alerts are dashboard evidence, not desktop notifications or webhooks.

## Configuration and bounded external work

New defaults:

```json
{
  "ai_summary": {
    "enabled": false,
    "model": "claude-haiku-4-5",
    "include_project_names": false
  },
  "project_paths": {}
}
```

Enabled AI summaries use `ANTHROPIC_API_KEY` with the Messages API. The payload
allowlist contains numeric aggregates, date labels and a session count. Prompt
text, session titles, session IDs and model labels are not sent. Project names
are included only with the separate explicit opt-in. Both request and response
are capped at 64 KiB; output requests at most 600 tokens with a 15-second timeout.
Missing credentials and service failure produce an unavailable state.

Status has a three-second timeout and a 64 KiB response cap. The local browser
does not wait for this request before rendering usage.

Project directories require an explicit `project_paths` mapping from the stored
project label to an absolute path. A basename alone cannot safely identify a
repository. Without a mapping the brief still returns the last title and explains
that filesystem evidence is unavailable. Git status disables optional locks and
fsmonitor, uses no shell and is capped at 64 KiB and three seconds. `TODO.md`
must stay inside the mapped directory, cannot be a symlink and is capped at
64 KiB with the first fifty unfinished checkboxes returned. No prompt ingestion
or arbitrary repository-file scan was added.

External API references checked during implementation:
[Anthropic, 2026: API overview](https://platform.claude.com/docs/en/api/overview)
and [Anthropic, 2026: Status API](https://status.claude.com/api/v2).

## Replacements and scope

- Replaced only the Wrapped route's placeholder because Phase 6 now implements
  that view. The shared Placeholder remains for unknown routes.
- Updated the API module's deferral description because Wrapped is now available.
- No services, dependencies, source files or stored records were removed.
  No new dependency or database migration was needed.
- React production serving and wheel packaging remain Phase 7. Existing
  repository-wide lint/formatting debt and the bundle warning remain deferred.
- No AI summary was enabled or sent for the developer's account. All checks use
  fixtures, temporary stores and mocked external transports.

## Validation

Final Phase 6 Gate, independently rerun by the plan reviewer:

```text
uv run pytest -q tests/test_wrapped.py tests/test_alerts.py tests/test_phase6_services.py
25 passed, 2 warnings in 1.76s
```

Full-suite run before the two final pricing test additions:

```text
uv run pytest -q
112 passed, 2 warnings in 4.23s

npm run build
✓ built in 6.20s

npm run test
Test Files  1 passed (1)
     Tests  3 passed (3)

npm run e2e
30 passed (16.2s)

Changed Python files: uv run ruff check <changed paths>
All checks passed!

New modules and tests: uv run ruff format --check <paths>
8 files already formatted

uv run ruff check .
Found 21 errors.

Sync idempotence: PASS (2, 0.00039999999999999996, 40)
Doctor fixture smoke: PASS; exit=0; coverage reported; quota disabled
```

The repository format gate initially reported 21 unformatted files. Six new
Phase 6 files were formatted; the fifteen previously documented existing files
remain unchanged. The full lint gate reports only the same twenty-one existing
errors outside the Phase 6 diff. Two dependency deprecation warnings and the
existing Vite bundle-size warning remain.

Browser checks cover status-failure rendering, disabled summary behavior,
Wrapped snapshot-backed data, download, reload, loading/error/empty states and
zero horizontal overflow at 1440/1024/768/390px. Existing core and analysis browser
checks also pass. The final offline-status and alert-transaction guards were
verified by the focused Python gate after the browser run.

Independent review and its follow-up found no remaining correctness gaps.
