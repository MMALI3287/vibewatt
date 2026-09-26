# Data sources

The authoritative version of this lives in `PLAN.md` section 2. This file records
what was tried, what worked and what did not, so nobody re-derives it.

## Coverage

| Surface | Per-response tokens | Per-session totals | In plan utilization |
|---|---|---|---|
| Claude Code local | yes, JSONL | derived | yes |
| Cowork local | yes, audit.jsonl | derived | yes |
| Claude Code web | no | **yes, session API** | yes |
| Cowork remote | no | **yes, session API** | yes |
| claude.ai chat | no | no | yes |

## Verified findings

- **Content-block repetition.** Claude Code writes one line per content block and
  repeats the whole-response `usage` on each. Measured 2.8x inflation of
  input/output on a real Claude Code session (2.1x across all token types) and
  1.98x on a Cowork tree. Dedup on `(message.id, requestId)` and keep the
  per-field maximum, because the first line is a streaming placeholder. Full
  rule in PLAN.md section 2.2.
- **Cache write TTL split.** `cache_creation.ephemeral_1h_input_tokens` bills at 2x
  base input, `ephemeral_5m_input_tokens` at 1.25x. Using the flat
  `cache_creation_input_tokens` understated one real session by 38%. A record
  with only the flat total is priced as 5 m, the API default when no TTL is
  requested. A split that sums to less than the total puts the rest on 5 m too,
  so no written token is dropped (A-063).
- **Session titles.** Claude Code writes `custom-title` (`customTitle`) when the
  user names a session and `ai-title` (`aiTitle`) when it names one itself;
  neither carries a timestamp. vibewatt keeps one title per session, best kind
  first: custom-title, ai-title, `last-prompt`, then the first user message.
  `summary` records no longer appear in current logs.
- **Cloud session usage is reachable.** `list_sessions` returns
  `external_metadata.usage` with `cost_usd` and token counts per session, plus
  `title`, `origin`, repo, `context_usage` and `rate_limit_info`. Five sessions on
  one real account carried $120 of web usage absent from every local log.
- **Titles are already on disk.** `last-prompt` records carry the prompt text. 73
  recovered from a single session. No API call needed.
- **ccusage 20.0.20 reports zero** for current-generation models: its embedded
  pricing table stops at `claude-opus-4-8`. Verified against 218 real assistant
  records across `--mode display|calculate|auto`. This is why the built-in table
  outranks any fetched one.

## Other inputs (Phases 5-6)

- **Tool reads.** `Read` tool calls in Claude Code logs, for the repeated-read
  finding. Only a keyed hash of `(session, path)` is stored: an HMAC under a
  per-install random key in the store's `meta`, so a guessed path cannot be
  confirmed from a copied store (A-121).
- **Session titles.** See above: one title per session.
- **Desktop plan history.** `plan-usage-history.json`, read only, version 2. See
  PLAN.md section 2.5.

## Outbound calls

Every request vibewatt makes. Each has a size cap and a timeout; none is made
from a page request unless noted.

| Call | Method, URL | Sends | Cap, timeout | When |
|---|---|---|---|---|
| Pricing table | GET `raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json` | nothing | 16 MiB, 10 s | CLI reports, explicit CLI sync and `serve` startup, cached 24 h; `--offline` skips |
| Plan utilization | GET `api.anthropic.com/api/oauth/usage` | Claude Code OAuth token | 64 KiB, 10 s | fallback only, at most every 10 min, backs off on 429; never from a page |
| Service status | GET `status.claude.com/api/v2/summary.json` | nothing | 512 KiB, 3 s total | dashboard banner, cached 5 min; not when offline |
| AI weekly summary | POST `api.anthropic.com/v1/messages` | `ANTHROPIC_API_KEY`, weekly aggregates | 64 KiB each way, 15 s | a button click with `ai_summary.enabled`; off by default |

## Provenance and freshness

The dashboard labels shared groups of figures by origin. Local token counts are
`computed_local`; their API-equivalent costs are `estimate`. Harvested session
tokens and cost are `cloud_reported`. Quota readings are `official`; forecast
bands, pace and savings are computed estimates rather than official limits.

Local `as_of` is the last sync timestamp, not the current page-request time.
A cloud session uses its reported update time or an unavailable timestamp.
Wrapped exposes separate local-sync and cloud-harvest times because a single
recent timestamp would hide a stale source. Analysis computation time is shown
separately from those input timestamps. Quota freshness uses the retained sample
time. No timestamp implies complete account-wide token coverage.

Browser polling runs every 60 seconds only while visible and online. It
refreshes active store views including cached quota. It does not invoke sync,
external service-status calls or paid summaries. Session lists with multiple
loaded pages pause polling to bound repeated work. The versioned CLI snapshots
are also store-only; see [AGENT-JSON.md](AGENT-JSON.md).

Stored local costs are repriced atomically during sync when the fingerprint of
loaded pricing inputs changes. The fingerprint includes overrides and billing
multipliers. Repricing works after transcript pruning and never substitutes
local estimates for cloud-reported cost. Pricing is loaded at CLI sync/report
and server startup; a long-running server uses that loaded pricing snapshot.

## Not found

- **A documented REST endpoint for listing sessions.** `/api/oauth/usage` is the
  account-level one and is used by `vibewatt/quota.py`. The per-session listing is
  served through the `claude-code-remote` MCP tool over an internal channel; no
  public equivalent was found in the docs or in the container's environment. The
  `tags` filter that would list Cowork sessions is rejected for in-session callers
  and documented as OAuth-only.
  **Current workaround:** `vibewatt harvest --file sessions.json` ingests a saved
  listing.
  **Re-spiked in phase 2:** the `claude-code-remote` MCP tool (`list_sessions`)
  is not present in a plain Claude Code session — it's only reachable from
  specific hosted contexts, not a general HTTP endpoint this codebase can call.
  No new direct endpoint found. `--file` harvest stays the only ingestion path;
  revisit if Anthropic documents the per-session listing endpoint.

## Retention

`cleanupPeriodDays` defaults to 30 and cleanup runs at **every** Claude Code
startup. Setting it to `0` disables transcript writing entirely rather than
keeping them forever — it is a trap. Recommend 3650. Nothing can recover what was
already deleted; the SQLite store only preserves from first sync forward.
