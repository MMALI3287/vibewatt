# Changelog

Each release lists what changed for people who use vibewatt. The reasons behind
each change and every removal are recorded in [docs/PLAN.md](docs/PLAN.md).

## 0.4.0 (2026-10-06)

The first full release on PyPI. The earlier 0.0.1 upload only reserved the name.

### Providers

- **Codex** usage from `~/.codex` rollouts, priced at OpenAI's published rates,
  plus the 5-hour and weekly plan windows Codex logs.
- **GitHub Copilot Chat** usage from VS Code's chat-session store, with the
  credits GitHub bills and premium-request readings from Copilot's local cache.
- **Google Antigravity** usage from its local conversation databases, read only.
- A provider filter. "All providers" splits every figure by source and "All
  Claude surfaces" keeps the Claude-only view. Other providers' plan readings
  have their own meters and never mix with Claude's.

### Claude Code and Cowork

- One row per response with the largest value seen for each field, so output is
  neither inflated by repeated log lines nor undercounted by placeholder counts.
- Cache writes priced by TTL. Unknown models are reported as unpriced, never $0.
- Usage is priced at the rate in effect when it happened, so a later price
  change does not rewrite past costs.
- Plan utilization from the status line, the desktop app's history or the usage
  endpoint, with pace and a projected range at reset.
- Cloud session totals for Claude Code on the web and Cowork remote via
  `vibewatt harvest`.
- Several Claude accounts kept apart. `export` and `import` move usage between
  machines.

### Dashboard and reports

- React dashboard bundled in the wheel. No Node needed to run it.
- Overview, sessions, projects, models, analysis and Wrapped views in light and
  dark themes, tested for keyboard use and WCAG AA contrast.
- Findings for cost anomalies, cache opportunities, token waste, peak windows
  and context size, each with its evidence.
- PNG export, opt-in browser alerts, terminal, JSON and CSV output and a Claude
  Code status line.

### Upgrading

- A store from an earlier build is migrated in place after a backup.
- Data from the old `ccburn` name is copied on first run. `CCBURN_*` variables
  and `ccburn.json` still work for this release with a warning.
