# Feature matrix

Every feature I found across the four reference tools, and whether vibewatt actually
has it. Legend:

- **done** — implemented and exercised by a test or a real run
- **partial** — works but narrower than the reference implementation
- **no** — not implemented
- **n/a** — does not apply to a CLI + local web app

Sources: [ccusage](https://github.com/ccusage/ccusage) (MIT),
[aqua5230/usage](https://github.com/aqua5230/usage) (AGPL-3.0),
[bozdemir/claude-usage-widget](https://github.com/bozdemir/claude-usage-widget) (MIT),
[kalatsch/claude-activity](https://github.com/kalatsch/claude-activity) (MIT).

## Data sources

| Feature | From | vibewatt | Note |
|---|---|---|---|
| Claude Code local logs | all four | **done** | `~/.claude/projects/**/*.jsonl` |
| `CLAUDE_CONFIG_DIR`, multi-path | ccusage | **done** | splits on `os.pathsep` |
| **Cowork local sessions** | none | **done** | nobody else reads these |
| Account-wide plan utilization | widget | **done** | `/api/oauth/usage`, the only web-inclusive figure |
| Statusline-fed rate limits | widget | **done** | zero-cost source, stale windows clamped |
| macOS Keychain token lookup | widget | **partial** | coded, untested (no macOS here) |
| Codex / Gemini / 15 other CLIs | ccusage, usage | **no** | out of scope; this is Claude-only |
| Cursor `state.vscdb` | aitrack | **no** | |
| **Claude Code web per-session usage** | none | **done** | `vibewatt harvest` — via the session API, not the container |
| **Cowork remote per-session usage** | none | **partial** | same path; tag filter needs an OAuth caller |
| Session titles ("what you worked on") | usage | **done** | from `last-prompt` records, no API call |
| SQLite store | none | **done** | `vibewatt sync`, idempotent upserts |
| Session-grouped report | ccusage | **done** | `vibewatt sessions`, local + cloud merged |

## Accuracy

| Feature | From | vibewatt | Note |
|---|---|---|---|
| Deduplicate repeated rows | ccusage | **done** | keyed on `(message.id, requestId)`; 2.8x inflation avoided |
| Cache write priced per TTL (5m vs 1h) | none | **done** | others use one flat multiplier; 38% error |
| Fast mode pricing | none | **done** | `speed: "fast"` doubles Opus rates |
| `inference_geo: "us"` 1.1x | none | **done** | |
| Web search billed per call | none | **done** | $10 / 1,000 |
| Subagent tokens tracked separately | ccusage issue #22625 | **done** | counted apart, `--no-sidechains` excludes |
| Remote pricing refresh | ccusage | **done** | LiteLLM, cached 24h, **gap-fill only** |
| Custom pricing overrides | ccusage | **done** | `pricing_overrides` in config |
| Historical price snapshot per day | claude-activity | **done** | stored cost is frozen per day |
| Unknown models reported not zeroed | none | **done** | ccusage 20.0.20 silently zeroes current models |
| `costUSD` from log when present | ccusage `--mode` | **no** | current Claude Code no longer writes it |
| Above-200k context pricing tier | ccusage | **no** | current models are flat-rate to 1M |

## Reports

| Feature | From | vibewatt | Note |
|---|---|---|---|
| Daily rollup | all four | **done** | |
| Per-model breakdown | all four | **done** | |
| Per-project / instance grouping | ccusage, usage, widget | **done** | with aliases |
| Per-source breakdown | none | **done** | Claude Code vs Cowork |
| Hour-of-day grid | claude-activity, widget | **done** | not filterable, labelled as such |
| 52-week contribution heatmap | usage, widget, claude-activity | **done** | metric switchable |
| 5-hour rate-limit blocks | ccusage | **done** | |
| Burn rate + projection to close | usage, widget | **done** | |
| Streaks, peak day | usage | **done** | labelled local-only |
| Cache hit rate | widget, usage | **done** | |
| Month-to-date spend | widget | **done** | |
| Plan vs API-equivalent savings | usage, widget | **done** | `--plan 20` shows the multiple |
| Weekly / monthly report commands | ccusage | **no** | filter by range instead |
| Session-grouped report | ccusage | **done** | `vibewatt sessions` |
| "What you worked on" titles | usage | **done** | `last-prompt` records carry it |
| Wrapped / Year in Review | usage | **no** | |

## Output and integration

| Feature | From | vibewatt | Note |
|---|---|---|---|
| Terminal tables + colour | ccusage, usage | **done** | |
| Standalone HTML report | usage, claude-activity | **done** | offline, data embedded |
| Live local dashboard | claude-activity | **done** | `vibewatt serve` |
| JSON output | all four | **done** | |
| CSV export | usage, widget | **done** | |
| Localhost JSON API | widget | **done** | `/api/usage`, `/api/dataset` |
| Statusline output | ccusage, usage, widget | **done** | `vibewatt statusline` |
| Project-name masking | usage | **done** | `--mask-projects` |
| Config file | ccusage, widget | **done** | platform-correct locations |
| Diagnostics command | none | **done** | `vibewatt doctor` |
| PNG export | usage, widget | **no** | screenshot the page |
| Compact table mode | ccusage | **partial** | responsive, no `--compact` flag |
| Webhooks (Slack/Discord) | widget | **no** | |
| Desktop notifications | usage, widget | **no** | |

## Live / desktop surface

| Feature | From | vibewatt | Note |
|---|---|---|---|
| Auto-refresh | widget, usage | **partial** | dashboard rebuilds per request; no push |
| Menu bar / tray / OSD overlay | usage, widget | **no** | deliberate: no headless or cloud story |
| Live token/min badge | widget | **partial** | burn rate shown, not a live badge |
| Per-turn cost ticker | widget | **no** | |
| Subagent count badge | widget | **partial** | totals shown, not a live counter |
| Themes beyond light/dark | usage (14), widget (11) | **no** | |
| Localization | usage (5 languages) | **no** | |
| Single-instance guard | widget | **no** | |
| Update notifications | widget | **no** | |

## Analysis

| Feature | From | vibewatt | Note |
|---|---|---|---|
| Durable history surviving pruning | claude-activity | **done** | |
| Monthly budget cap | widget | **partial** | config key read, no alerting |
| Anomaly detection | widget | **no** | |
| Burn / spike alerts | widget | **no** | |
| Prompt-cache opportunity scanner | widget | **no** | needs prompt text |
| Cost optimisation tips | widget | **no** | |
| AI-written weekly summary | widget | **no** | would need an API call |
| Token-waste health check | usage | **no** | |
| Peak-window awareness | widget | **no** | |
| Context-window nudges | usage | **no** | |
| Service status alerts | usage | **no** | |
| Progress concierge / token saver | usage | **no** | not a usage feature |
| News ticker | widget | **no** | |

## Score

Counting only rows that apply: **38 done, 6 partial, 28 not implemented** out of 72.

The "no" column is concentrated in the always-on desktop surface (tray icons,
notifications, tickers, themes) and in analysis features that need prompt text or
an API call. If you want the tray widget, `bozdemir/claude-usage-widget` already
does it well and is MIT.

What vibewatt has that none of them do: Cowork, per-TTL cache pricing, fast-mode and
geo pricing, unknown-model honesty, account-wide quota shown next to local history,
interactive filters, and `vibewatt doctor`.
