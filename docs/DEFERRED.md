# Deferred work

Reconciled against the code and `PLAN.md` on 2026-09-26. Every item is now
scheduled into a phase or closed with a reason. The build plan with acceptance
checks is [COMPLETION-QUESTIONS.md](COMPLETION-QUESTIONS.md). Historical audit
findings remain in `PLAN.md` as evidence, not as an active backlog.

## Scheduled for Phase 8 (backlog completion)

The PR that finishes an item removes its row here and records the removal
under "Phase 8" in `PLAN.md`.

| Item | Package |
|---|---|
| macOS CI and clean-install checks on three platforms | 5 |

## Scheduled for Phase 10 (more providers)

| Item | Prerequisite |
|---|---|
| ChatGPT/Codex CLI usage | 95 real sessions exist locally. Write the response identity, cumulative-versus-per-turn and retention contract into `DATA-SOURCES.md` before parsing |
| Gemini CLI and Antigravity usage | No local data exists yet. Sanitized sessions are needed before a parser is written |

## Closed on 2026-09-26

Reasons and reopen triggers are in `PLAN.md` under "Phase 8" and in
`COMPLETION-QUESTIONS.md`.

- Advisor-model pricing (the detection guard stays in Phase 8)
- Opt-in cloud session listing
- Optional OTLP receiver
- Localization
- Drain explainer and metering drift
- Spend linked to commits and finding follow-up outcomes
- React 19, Router 8 and TanStack Table 9
- Webhooks and desktop (OS) notifications. Browser notifications stay in Phase 8
- npm name reservation

## Already resolved or outside the backlog

- Post-phase implementation (2026-09-26) moved eight items out of the active
  backlog because they are implemented: retained-cost repricing, DST-aware local
  timezone, Overview plan comparison, header session search, date presets,
  bounded browser refresh, provenance/freshness labels and versioned CLI JSON.
  The implementation and removal record are in `PLAN.md` under "Post-phase
  deferred essentials". No existing service, data table or command was removed.
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
  Publishing a release is Phase 11 and needs the user's confirmation at each step.
