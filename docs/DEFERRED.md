# Deferred work

Reconciled against the code and `PLAN.md` on 2026-09-25. Historical audit findings
remain in the plan as evidence, not as an active backlog. These items are not
part of Phase 7.

## Correctness and data coverage

| Item | Prerequisite and recommended next step |
|---|---|
| Reprice stored turns after pricing changes | Decide whether historical estimates track corrected rates. Add a per-turn pricing version and a transactional reprice pass, preserving cloud-reported costs. Back up the store before migration. |
| Resolve `timezone: local` to a DST-aware IANA zone | Add winter/summer boundary regressions on Windows and Linux. Explicit IANA configuration already works and is a workaround. `resolve_tz()` still returns the current fixed offset for local. |
| Advisor-model usage in `usage.iterations[]` | Sanitized fixtures plus a documented pricing and dedup rule for nested usage. |
| Historical Opus 4.6/4.7 fast-mode pricing | Verify dated official rates and record effective periods before changing historical estimates. |
| Historical over-200k context premiums | Verify model/date thresholds and obtain fixtures with the necessary context evidence. |
| Web-fetch and code-execution usage | Establish which log fields and billed quantities are available; do not infer absent calls. |
| Missing model rows, including Mythos preview and retired Sonnet 3.5/3.7 | Recheck official model IDs and rates; unknown models remain explicitly unpriced until then. |
| Local context-window nudges | A trustworthy per-session current context snapshot and maximum are required. A rate-limit block cannot supply them. Cloud snapshot nudges already work. |
| Codex, Gemini and other CLI providers | Pick the first provider and supply sanitized fixtures. Define response identity, pricing, coverage and the discover/parse contract before implementation. |
| Multi-account and multi-machine support | Define account identity and merge/conflict rules. Existing multi-root discovery is not account isolation. Export/import should remain local and bounded. |
| `history.jsonl` activity backfill | Read only timestamps and projects, never prompt text. Define duplicate handling and an ingestion size bound before enabling it. |
| Opt-in cloud session listing | Verify access to the undocumented `/v1/code/sessions` endpoint and cap pagination/response sizes. Keep file harvest supported. Authentication is needed only for a real integration trial. |
| Optional OTLP receiver | Define local bind, request limits and provenance. Use it for attribution; JSONL remains the token source of truth. |

## Dashboard and integrations

| Item | Prerequisite and recommended next step |
|---|---|
| Overview plan-versus-API multiple | Expose the configured monthly plan price and define the comparison period. Wrapped's annual multiple does not cover this. |
| Global header search and `/` shortcut | Agree on searchable fields and index/server-search threshold. Sessions search already works. |
| Date-range presets | Expose report-zone `today` including `day_start_hour`; do not derive boundaries from the browser clock. |
| Browser live refresh | Choose bounded polling or SSE and cache invalidation. Background store sync already exists; it does not refresh an open browser by itself. |
| Dashboard PNG export | Choose whole-page versus selected-view export and masking defaults. Wrapped already exports an SVG card. |
| Webhooks, desktop/browser notifications | Choose destinations and opt-in delivery rules, retries and dedup. Existing alerts are dashboard evidence. Credentials or OS/browser permission are only needed for the selected integration. |
| Localization | Choose initial languages and a translation workflow; audit dates, numbers and accessible labels. |
| Drain explainer and metering drift | Collect sufficiently long quota history with version/regime provenance. Do not infer plan limits from tokens. |
| Provenance badges and `as_of` on figures | Define official/local/cloud/estimate semantics and timestamps in API schemas first. |
| Spend linked to commits and finding follow-up outcomes | Choose a bounded read-only Git mapping and privacy rules; do not infer causation from nearby timestamps. |
| Stable agent-facing `status --json` and `quota --json` | Specify schema versions, unavailable states and exit codes. An MCP server remains a non-goal for now. |
| React 19, Router 8 and TanStack Table 9 | Separate compatibility work with browser regressions. Phase 7 upgrades Router to 7 and keeps React 18/Table 8. |

## Already resolved or outside the backlog

- Phase 5 added persisted findings.
- Phase 6.5 fixed redirected Windows output, report-date handling, repeated quota
  fetches, filtered retained history, title selection and the audited data/security/UI bugs.
- Phase 7 handles React serving, build assets, route bundles, dependency advisories,
  legacy renderer removal, documentation and repository lint/format debt.
- The old `dashboard.py` was removed in Phase 2 because FastAPI replaced its
  duplicate pipeline. Its refresh script must not be resurrected.
- `ui.py`, the `html` command and legacy JSON endpoints were removed in Phase 7
  because the React app and typed endpoints replace their unsafe embedded renderer.
- Tray/desktop hosting, multi-user authentication and recovery of already deleted
  transcripts remain non-goals, not promises for a later phase.
- PyPI `vibewatt` is reserved at 0.0.1 and the GitHub repository is renamed.
  npm's registry returns 404 for `vibewatt`; reserving it and publishing a release
  are external actions requiring authorization. npm reservation is not needed
  for the Python wheel or for deferred development.

## Before starting

No account setup or service deployment is required for ordinary local backlog
work. Choose one bounded item first. Prioritize repricing and local-DST handling,
then the missing pricing coverage. Add a failing regression for each correctness
fix and back up the store before migrations. Provider/cloud/integration work needs
its specific data or access only when that item starts. Every automatic collector
needs a bound and an observed real cycle before being enabled.
