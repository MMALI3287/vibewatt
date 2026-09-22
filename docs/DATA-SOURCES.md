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
  repeats the whole-response `usage` on each. Measured 2.8x inflation on a real
  Claude Code session and 1.98x on a Cowork tree. Dedup on `(message.id, requestId)`.
- **Cache write TTL split.** `cache_creation.ephemeral_1h_input_tokens` bills at 2x
  base input, `ephemeral_5m_input_tokens` at 1.25x. Using the flat
  `cache_creation_input_tokens` understated one real session by 38%.
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
