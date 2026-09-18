# Phase 4: core views

Phase 3 was verified before implementation: build passed, 3 unit tests passed
and all 5 existing browser checks passed.

## Delivered

- Sessions: server-side search across titles, projects and models, cursor-based
  infinite scroll and a keyboard-accessible detail dialog at `/sessions/:id`.
  Back restores the originating list and direct links work after reload.
- Projects and Models: sortable, paginated tables, filtered KPI rows and session
  drill-down. Project breadcrumbs retain the active filters.
- Overview: daily heatmap with fourth-root intensity, hour-of-day chart and
  estimated usage blocks. The URL metric switches cost/tokens. Charts have
  tooltips and table equivalents.
- Session API: report-timezone date filtering, stable timestamp-plus-ID cursors,
  full-title search, typed turn responses, unpriced-response counts and harvested
  context usage. Legacy timestamp cursors remain accepted.

Local session totals use matching turns. Detail always shows the full session.
Cloud sessions are selected by their start date and retain their API cost because
daily and per-response attribution are unavailable. Charts and KPIs are explicitly
local-only. Context is the latest harvested snapshot, not an invented peak.

## Dependencies and replacements

- Added TanStack Table v8 for sorting/pagination and Recharts for the hourly
  chart, matching the planned stack. The initial v9 table resolution was replaced
  with v8 because its API is incompatible with the implemented v8 interface;
  its unused transitive store packages were removed by npm during that change.
- Replaced only the Sessions and Projects placeholder routes with working views.
  The Placeholder component remains for later phases and unknown routes.
- Removed the hero's claim that its totals include harvested sessions because
  the summary endpoint reports local logs only.
- Removed the 58-character session-title truncation from store results so search
  and detail can use the full existing title. No additional prompt content is read.
- Suppressed duplicate harvested list entries when the same ID has local turns.
  This matches the existing detail endpoint's local-first selection and prevents
  duplicate list keys. No stored records or schema tables were removed.
- Replaced untyped turn dictionaries in the OpenAPI contract with a model that
  preserves all existing turn fields.

## Validation

Browser fixtures use an isolated temporary store with local logs and 45 harvested
sessions sharing a start timestamp. No developer logs or production store are read.

Phase 4 browser gate output:

```text
16 passed (10.0s)
```

Additional checks: production build passed, 3 frontend unit tests passed and
46 Python tests passed. The Python tests include session date boundaries in JST,
equal-timestamp pagination, full-title search, invalid cursors and unpriced turns.

The gate checks KPI recomputation, filtered URL restoration, modal deep links,
Back and Escape behavior, focus restoration, infinite scrolling, search beyond
the first page, preserved cloud cost/context, error recovery and zero horizontal
page overflow at 1440/1024/768/390px.

The fresh `plan-reviewer` agent could not start because of an account usage limit.
The existing investigation agent successfully performed the independent review
using the same review instructions. It found one gap: cloud-only filter choices
were missing. The complete `/api/session-facets` endpoint and selector integration
fixed it, with API and browser regressions. Follow-up verdict: no remaining
correctness findings. Review used the recorded passing gate results.

## Existing limitations

- Repository-wide Ruff still reports the 21 pre-existing errors documented in
  `PLAN.md`. Existing formatting debt also remains outside this phase.
- `ccburn serve` still serves legacy HTML. React production serving and packaging
  remain Phase 7 as already recorded in `PLAN.md`; use the existing Vite workflow
  for these views.
- The production build warns that the chart/table bundle exceeds 500 kB.
- npm reports 7 dependency audit findings. Dependency upgrades are separate from
  this phase and were not applied with a force upgrade.
