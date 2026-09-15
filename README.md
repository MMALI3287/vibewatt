# ccburn

Token usage, cost and plan utilization for **Claude Code** and **Claude Cowork**, read
from data already on your machine. No account setup, no API key, no telemetry.

```bash
pip install ccburn        # or: pipx install ccburn / uv tool install ccburn

ccburn                    # terminal report
ccburn serve              # live dashboard in your browser
ccburn html --out u.html  # standalone HTML report
ccburn json | jq .        # machine readable
```

Works on Windows, macOS, Linux and headless cloud boxes. Pure Python standard
library, zero dependencies, no compiler and no native wheels.

---

## What it covers, honestly

| Surface | Token-level history | Counted in plan utilization |
|---|---|---|
| Claude Code (local) | yes, from `~/.claude/projects` | yes |
| Cowork (local) | yes, from the desktop data dir | yes |
| Claude Code on the web | **no** | yes |
| Cowork remote sessions | **no** | yes |
| claude.ai chat | **no** | yes |

Web and remote sessions run in throwaway cloud containers. Their logs are created
inside that container and destroyed with it, so no local tool can read them — ccburn
included, and every other tool in this space likewise.

What *does* account for them is **plan utilization**: your 5-hour and 7-day allowance
is charged account-wide, whichever surface spent it. ccburn reads that from the same
endpoint Claude Code itself uses and shows it beside the local history. It is a
percentage, not a token ledger, so the two views complement each other rather than
one replacing the other.

Anything ccburn cannot see, it says so rather than reporting zero.

## Features

**Reporting**
- Daily calendar heatmap, 53 weeks, GitHub style
- Per-model, per-source, per-project and per-hour breakdowns
- Cost split across input, 5-minute cache write, 1-hour cache write, cache read and output
- Cache hit rate, streaks, peak day, month-to-date spend
- Terminal, standalone HTML, live dashboard, JSON and CSV

**Live**
- Plan utilization meters (5-hour and 7-day) with reset countdowns
- Current rate-limit window: spent so far, burn rate, projection to window close
- `ccburn statusline` for Claude Code's statusLine hook, tmux or Starship
- `ccburn serve` exposes `GET /api/usage` for any bar or widget

**Durability**
- Stored rollups survive Claude Code's 30-day log pruning
- Live logs stay authoritative; stored rows only fill days the logs no longer reach
- Historical cost is frozen per day, so a price change does not rewrite last quarter

**Correctness**
- Content-block rows collapsed per response (see below)
- Cache writes priced per TTL, not with one flat multiplier
- Fast mode, `inference_geo: "us"` and server-side web search priced
- Unknown models counted in tokens and reported, never silently priced at zero

## Three things this gets right

These are the differences that move the number, all measured on real session data.

**1. Content-block rows are collapsed.** Claude Code writes one JSONL line per content
block of a response, and every one of those lines repeats the same whole-response
`usage` object. Summing the lines multiplies your token count by however many blocks
the response had. On a real session that was a **2.8x** inflation. ccburn keys each
response on `(message.id, requestId)` and counts it once.

**2. Cache writes are split by TTL.** A 1-hour cache write costs 2x base input; a
5-minute write costs 1.25x. The log reports both separately under `cache_creation`.
Reading only the flat `cache_creation_input_tokens` total understated one real
session's cache-write line by **38%**.

**3. Current models are actually priced.** Rates are transcribed from Anthropic's
published pricing page and live in `ccburn/pricing.py`. A community pricing table
(LiteLLM) is fetched and cached, but only ever to *fill gaps* — it never overrides a
verified rate. Community tables lag new releases, and a lagging table is worse than no
table: it prices a current model at zero instead of admitting it does not know.

## Where it reads from

| Source | Path |
|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (override: `CLAUDE_CONFIG_DIR`) |
| Cowork | `<desktop data dir>/{local-agent-mode-sessions,claude-code-sessions}/**/audit.jsonl` (override: `CCBURN_COWORK_DIR`) |

Desktop data dir is `%APPDATA%\Claude` on Windows, `~/Library/Application Support/Claude`
on macOS and `~/.config/Claude` on Linux.

Cowork renames three envelope fields (`session_id`, `_audit_timestamp`, `_audit_hmac`)
but keeps the `message.usage` payload identical to Claude Code's, so normalizing the
envelope is enough to price both together.

## Commands

```
ccburn report        terminal report (default)
ccburn serve         live dashboard        --host --port --refresh --no-browser
ccburn html          standalone HTML       --out
ccburn json          full data as JSON     --out
ccburn csv           per day per model     --out
ccburn blocks        recent rate-limit windows
ccburn statusline    one compact line
```

Shared flags: `--source {claude-code,cowork,all}`, `--since YYYY-MM-DD`, `--days N`,
`--tz Asia/Tokyo`, `--weeks N`, `--session-hours N`, `--no-sidechains`, `--by-project`,
`--mask-projects`, `--no-quota`, `--no-history`, `--offline`, `--no-color`.

## Configuration

Optional JSON, read from `%APPDATA%\ccburn\ccburn.json` on Windows,
`~/Library/Application Support/ccburn/ccburn.json` on macOS,
`${XDG_CONFIG_HOME:-~/.config}/ccburn/ccburn.json` on Linux, or `./.ccburn/ccburn.json`
per project. Point `CCBURN_CONFIG` at a file to override.

```json
{
  "timezone": "Asia/Tokyo",
  "session_length_hours": 5,
  "monthly_budget_usd": 200,
  "mask_projects": false,
  "statusline_cache_path": "~/.claude/rate-limits.json",
  "project_aliases": { "-home-user-api": "API" },
  "pricing_overrides": { "claude-opus-5": { "input": 4.0, "output": 20.0 } }
}
```

`statusline_cache_path` points at a rate-limit dump written by your Claude Code
statusLine hook. When present it is used instead of calling the usage endpoint: it is
seconds-fresh while a session runs and costs no API call.

## Privacy

Everything is computed locally. Prompt text is never read, stored or transmitted —
ccburn only looks at the `usage` object and timestamps. The one network call is the
plan-utilization lookup, which sends your existing Claude Code OAuth token to
Anthropic's own endpoint and nothing else. Disable it with `--no-quota`. The pricing
refresh fetches a public JSON file and sends nothing. `--mask-projects` pseudonymises
project names for sharing a screenshot.

## Caveats

- **Cost is an estimate at list API rates.** On a Pro or Max subscription this is what
  the same tokens *would* have cost pay-as-you-go, not what you were billed.
- Claude Code prunes local logs after 30 days. ccburn's stored history covers days from
  the first run onward; it cannot recover what was pruned before you installed it.
  Raise `cleanupPeriodDays` in your Claude Code settings too.
- Heatmap intensity is total tokens, which cache reads dominate. The tables break the
  categories apart.
- The plan-utilization endpoint is undocumented. It is the same one Claude Code calls,
  but it can change without notice; ccburn degrades to local-only if it does.

## License

MIT
