# vibewatt

Token usage, cost and plan utilization for **Claude Code** and **Claude Cowork**, read
from data already on your machine. No account setup, no API key, no telemetry.

```bash
pip install vibewatt        # or: pipx install vibewatt / uv tool install vibewatt

vibewatt                    # terminal report
vibewatt serve              # live dashboard in your browser
vibewatt html --out u.html  # standalone HTML report
vibewatt json | jq .        # machine readable
```

Works on Windows, macOS, Linux and headless cloud boxes. Pure Python standard
library, zero dependencies, no compiler and no native wheels.

---

> **Renamed from ccburn.** The first run copies an existing ccburn data dir and
> store to the vibewatt location and leaves the old one in place. `CCBURN_*`
> variables and `ccburn.json` config files still work for one release, with a
> deprecation note.

## Read this first: Claude Code is deleting your history

Claude Code runs a cleanup **at every startup** and deletes session transcripts
older than `cleanupPeriodDays`, which defaults to **30**. Anything older is
already gone, and no tool can recover it.

```jsonc
// ~/.claude/settings.json   (%USERPROFILE%\.claude\settings.json on Windows)
{ "cleanupPeriodDays": 3650 }
```

**Do not set it to `0`.** The same field gates the write path, so zero means no
transcripts are written at all rather than "keep forever"
([claude-code#23710](https://github.com/anthropics/claude-code/issues/23710)).

`vibewatt doctor` tells you what your current setting is and what it is costing you.
vibewatt's store keeps every response from its first sync onward, after Claude Code
prunes the log. It cannot reach back before you installed it.

---

## What it covers, honestly

| Surface | Token-level history | Counted in plan utilization |
|---|---|---|
| Claude Code (local) | yes, from `~/.claude/projects` | yes |
| Cowork (local) | yes, from the desktop data dir | yes |
| Claude Code on the web | **yes, via `vibewatt harvest`** | yes |
| Cowork remote sessions | **yes, via `vibewatt harvest`** | yes |
| claude.ai chat | no | yes |

Web and remote sessions run in throwaway cloud containers, so their *logs* never
reach your disk. Their **usage totals** are a different matter: the Claude Code
session API reports per-session tokens, cost, title, model and repository for every
cloud session. `vibewatt harvest` ingests that, which no other tool does.

On one real account, five cloud sessions carried **$120 of web usage that appears in
no local log at all**.

What *does* account for them is **plan utilization**: your 5-hour and 7-day allowance
is charged account-wide, whichever surface spent it. vibewatt reads that from the same
endpoint Claude Code itself uses and shows it beside the local history. It is a
percentage, not a token ledger, so the two views complement each other rather than
one replacing the other.

Anything vibewatt cannot see, it says so rather than reporting zero.

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
- `vibewatt statusline` for Claude Code's statusLine: records plan utilization for free
- `vibewatt serve` exposes `GET /api/usage` for any bar or widget

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
the response had. On a real session that was a **2.8x** inflation of input and output.
vibewatt keys each response on `(message.id, requestId)` and counts it once, keeping
the largest value seen for each field: the first line of a streamed response carries a
placeholder output count; keeping it undercounted output by 24% on real data.

**2. Cache writes are split by TTL.** A 1-hour cache write costs 2x base input; a
5-minute write costs 1.25x. The log reports both separately under `cache_creation`.
Reading only the flat `cache_creation_input_tokens` total understated one real
session's cache-write line by **38%**.

**3. Current models are actually priced.** Rates are transcribed from Anthropic's
published pricing page and live in `vibewatt/pricing.py`. A community pricing table
(LiteLLM) is fetched and cached, but only ever to *fill gaps* — it never overrides a
verified rate. Community tables lag new releases, and a lagging table is worse than no
table: it prices a current model at zero instead of admitting it does not know.

## Where it reads from

| Source | Path |
|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (override: `CLAUDE_CONFIG_DIR`) |
| Cowork | `<desktop data dir>/{local-agent-mode-sessions,claude-code-sessions}/**/audit.jsonl` (override: `VIBEWATT_COWORK_DIR`) |

Desktop data dir is `%APPDATA%\Claude` on Windows, `~/Library/Application Support/Claude`
on macOS and `~/.config/Claude` on Linux.

Cowork renames three envelope fields (`session_id`, `_audit_timestamp`, `_audit_hmac`)
but keeps the `message.usage` payload identical to Claude Code's, so normalizing the
envelope is enough to price both together.

## Commands

```
vibewatt report        terminal report (default)
vibewatt serve         live dashboard        --host --port --no-browser
vibewatt doctor        what vibewatt can and cannot see, and why
vibewatt sync          parse local logs into the SQLite store
vibewatt harvest       ingest cloud session usage   --file sessions.json
vibewatt sessions      every session, local and cloud, with titles
vibewatt html          standalone interactive report  --out
vibewatt json          full data as JSON     --out
vibewatt csv           per day per model     --out
vibewatt blocks        recent rate-limit windows
vibewatt statusline    one compact line
```

### Filters

`vibewatt html` and `vibewatt serve` produce an interactive page. Everything derived
from local logs filters live in the browser, with no round trip:

- date range: 7d / 30d / 90d / 1y / all
- source: Claude Code or Cowork
- project, and model
- metric: cost, total tokens, output tokens or responses — the heatmap recolours
- sortable tables on every column

Plan utilization deliberately does **not** filter. It is an account-wide number
that cannot be sliced by project or source, and the panel says so on its face.

### Why your numbers may look wrong

| Symptom | Cause |
|---|---|
| Streak is 0 but you use Claude daily | Web, Cowork remote and claude.ai chat write no local logs. Only plan utilization counts them. |
| Fewer projects than you worked on | Logs older than `cleanupPeriodDays` were deleted. |
| Cost looks enormous | It is the API-equivalent, not your bill. Pass `--plan 20` to see the multiple your subscription saves. |
| Heatmap looks flat | Total tokens is dominated by cache reads. Switch the metric to cost or output. |

Run `vibewatt doctor` — it prints the last day found, today in your timezone, your
retention setting and per-source coverage, so the cause is visible rather than
guessed at.

Shared flags: `--source {claude-code,cowork,all}`, `--since YYYY-MM-DD`, `--days N`,
`--tz Asia/Tokyo`, `--day-start-hour H`, `--weeks N`, `--session-hours N`, `--plan 20`,
`--no-sidechains`, `--by-project`, `--mask-projects`, `--no-quota`, `--offline`, `--no-color`.

See [FEATURES.md](FEATURES.md) for an honest matrix of what is implemented against
the four reference tools, including the 28 features that are not.

## Configuration

Optional JSON, read from `%APPDATA%\vibewatt\vibewatt.json` on Windows,
`~/Library/Application Support/vibewatt/vibewatt.json` on macOS,
`${XDG_CONFIG_HOME:-~/.config}/vibewatt/vibewatt.json` on Linux, or `./.vibewatt/vibewatt.json`
per project. Point `VIBEWATT_CONFIG` at a file to override.

```json
{
  "timezone": "Asia/Tokyo",
  "day_start_hour": 6,
  "session_length_hours": 5,
  "monthly_budget_usd": 200,
  "plan_usd_per_month": 20,
  "heatmap_metric": "cost",
  "mask_projects": false,
  "statusline_cache_path": "~/.claude/rate-limits.json",
  "project_aliases": { "-home-user-api": "API" },
  "pricing_overrides": { "claude-opus-5": { "input": 4.0, "output": 20.0 } }
}
```

Plan utilization comes from, in order: `vibewatt statusline` (below), the Claude
desktop app's own usage history (read only) and the usage endpoint as a fallback
called at most every 10 minutes. To feed it live and for free, make vibewatt your
Claude Code status line in `~/.claude/settings.json`:

```json
{ "statusLine": { "type": "command", "command": "vibewatt statusline" } }
```

It prints your plan windows and records a sample. To keep an existing status line,
put that command in `statusline_chain` in vibewatt's config; its output is shown
first. `statusline_cache_path` still reads a saved dump if it was written in the
last 10 minutes.

## Privacy

Everything is computed locally and stored in one SQLite file in vibewatt's data
directory. What it keeps from your logs: token counts, model, timestamps, project
and session ids plus **one title per session**. The title is the session name Claude
Code shows (`custom-title` or `ai-title`) or, failing those, the latest prompt of the
session or its first message, up to 500 characters. No other prompt text, no response
text and no file contents are stored. Paths of files the agent read are kept only as
keyed hashes, to spot repeated reads.

The dashboard listens on 127.0.0.1 and refuses requests whose `Host` is not a loopback
name as well as cross-site requests that change state, so another web page in your browser
cannot read it or drive it.

Network calls, all optional (see [docs/DATA-SOURCES.md](docs/DATA-SOURCES.md)):

| Call | Sends | When | Off switch |
|---|---|---|---|
| Pricing table from GitHub (LiteLLM) | nothing | at most once a day | `--offline` |
| `api.anthropic.com/api/oauth/usage` | your Claude Code OAuth token | fallback for plan utilization, at most every 10 min | `--no-quota` |
| `status.claude.com` summary | nothing | dashboard status banner, cached 5 min | `--offline` |
| Anthropic Messages API | your `ANTHROPIC_API_KEY` and weekly totals (project names only if you allow them) | only when you click the AI weekly summary with `ai_summary.enabled` | off by default |

`--mask-projects` replaces project names with `project 1`, `project 2`, ... in every
view and export, for sharing a screenshot.

## Caveats

- **Cost is an estimate at list API rates.** On a Pro or Max subscription this is what
  the same tokens *would* have cost pay-as-you-go, not what you were billed.
- Claude Code prunes local logs after 30 days. vibewatt's store covers days from the
  first sync onward; it cannot recover what was pruned before you installed it.
- `day_start_hour` moves the day boundary for daily totals, streaks, the heatmap,
  Wrapped and anomalies. With `6`, a session from 20:00 to 04:00 counts as one day.
  The hour-of-day chart still shows clock hours.
  Raise `cleanupPeriodDays` in your Claude Code settings too.
- Heatmap intensity is total tokens, which cache reads dominate. The tables break the
  categories apart.
- The plan-utilization endpoint is undocumented. It is the same one Claude Code calls,
  but it can change without notice; vibewatt degrades to local-only if it does.

## License

MIT
