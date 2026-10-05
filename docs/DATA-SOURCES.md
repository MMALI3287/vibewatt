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

### Rates over time

A response is priced at the rate in effect on its UTC date. Verified dated
built-in rates live in `BUILTIN_PERIODS` and outrank the flat `BUILTIN` table.
For models only the community table knows, every refresh records a rate change
in `pricing-history.json` next to the cache, dated by when the table was fetched.
A later discount or price rise therefore never reprices older usage. Usage older
than the first observation takes the earliest recorded rate, since nothing older
exists. The file keeps at most 64 changes per model, always keeping the first.
Models newer than the built-in table take their context window from the
community table's `max_input_tokens` for local context nudges.

`vibewatt doctor` warns when the community table is more than 7 days old. A
weekly GitHub Actions job (`.github/workflows/pricing-drift.yml`) compares the
built-in table with the community table and opens or updates one issue when an
Anthropic model is missing or a rate differs. The issue asks for verification
against the official pricing page; nothing changes a rate automatically.

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


## Phase 8 pricing evidence (retrieved 2026-09-26)

Historical fast rates are bounded by verified dates. Opus 4.6 has a verified
$30/$150 period from 2026-05-12 through 2026-06-28, using the dated release note
that Opus 4.7 launched fast mode at the same rates. Opus 4.7 uses those rates
from 2026-05-12 through 2026-07-23. Earlier Opus 4.6 launch/promotion periods
remain unpriced because their effective rates were not verified from a dated
official source. Current Opus 4.8/5 rates are valid from their release dates.
Sources: [release notes](https://platform.claude.com/docs/en/release-notes/overview)
and [historical official pricing](https://platform.claude.com/docs/en/about-claude/pricing?38d7aa68_page=5&fcdaa149_page=1&fcdaa149_sort_date=desc&query=deliverability).

The >200,000 prompt-token premium is verified for Opus 4.6 from launch through
2026-03-12 and Sonnet 4 from 2025-08-12 through 2026-04-29. Prompt size includes
input, cache reads and both cache-write TTLs. Other unverified model/date
combinations above 200,000 tokens carry `context_premium_unknown`; that cost is
potentially understated. Sonnet 4.5 and pre-GA Sonnet 4.6 premium periods remain
unverified. Sources: [Opus launch](https://www.anthropic.com/news/claude-opus-4-6),
[Sonnet 1M launch](https://claude.com/blog/1m-context) and the release notes.

Web fetch has no per-request fee. Code execution counts do not establish billed
container time or eligibility for free usage so cost is labelled unavailable,
never zero. Non-message iterations are counted once per deduped response and
reported by doctor; their nested tokens are not added to top-level usage.
Source: [tool pricing](https://platform.claude.com/docs/en/about-claude/pricing).

## Phase 8 activity and local context

`history.jsonl` is read only during sync, with a total 50 MB / 500,000 record cap
across configured Claude roots. Only numeric timestamp (milliseconds since epoch)
and project string survive parsing. Malformed rows count toward the cap. Prompt
text, pasted content and all other fields are discarded before SQLite insertion.
Over-cap input is truncated and `doctor` reports the warning. Existing activity
is retained when source history disappears; duplicate timestamp/project pairs
are ignored. Activity does not imply measured billable usage.

Local context uses stored per-response input/cache counters, never rate-limit
blocks. On the Anthropic API, Opus 4.7 and later, Sonnet 5 and later and the
Fable models run with a native 1M window on every plan, so first-party ids of
those models use 1M. Provider ids (Bedrock, Vertex) can run at 200K and keep the
200K window until the session shows a larger prompt, as do models that reach 1M
only through a `[1m]` variant. A session started with
`CLAUDE_CODE_DISABLE_1M_CONTEXT=1` compacts at 200K, which local logs do not
record; its nudge stays quiet rather than firing early. Sources retrieved
2026-10-05: https://code.claude.com/docs/en/model-config#extended-context and
https://platform.claude.com/docs/en/build-with-claude/context-windows.

Opus 5.5 and Sonnet 5.5 rates were added in Phase 9 (retrieved 2026-10-05 from
https://platform.claude.com/docs/en/about-claude/pricing). Opus 5.5 cache reads
cost 0.05x base input. Its fast mode ($8/$40) is priced from 2026-09-24, the
release-notes date. Local logs show Opus 5.5 a day earlier; fast turns from that
day stay unpriced. Both models use standard pricing across the full 1M window.

## Account and machine transfer

The account UUID comes from `oauthAccount.accountUuid` in Claude Code local
configuration. Missing identity is `unknown`. A random machine UUID is created
once in vibewatt's user configuration directory. Each response stores both.
Account databases are separate; CLI `--account` and the dashboard account picker
select one account across all machines. Quota from another account is not read
or fetched. Previously attributed transcript files and activity remain owned by
their original account after a login switch.

`vibewatt export --out file.vwx` writes gzip JSON format version 1 containing
response inputs, retained history inputs and harvested session totals.
`--include-titles` explicitly includes session titles. Raw logs, prompt bodies,
credentials and quota are never exported. Import validates the full archive
before opening a writable store, with a 200 MiB compressed/expanded cap and a
500,000-record cap across every input table. Imported responses dedup by account,
machine and response identity, preserving the maximum of each usage counter.

## Codex (Phase 10, observed 2026-10-05)

Source: `$CODEX_HOME` (default `~/.codex`), `sessions/**/rollout-*.jsonl` and
`archived_sessions/rollout-*.jsonl`. Checked against 195 real rollouts from Codex
CLI 0.128 to 0.159, 2026-05-01 to 2026-10-03. Everything below was measured on
those files; a count says how many records it rests on.

**Where usage lives.** `event_msg` records with `payload.type = "token_count"`.
5,174 carry `info`; the rest only refresh `rate_limits`.

**Cumulative or per call.** Both are present. `info.total_token_usage` is the
running session total and never decreases (0 decreases in 5,174). `info.last_token_usage`
is the call that just finished. Summing the unique `last_token_usage` values
reproduces the final total exactly in 193 of 194 sessions. The one exception
differs only in `total_tokens`, never in a component. So the importer bills
`last_token_usage` and never sums `total_token_usage`.

**Response identity.** There is no message or request id. A response is
`(session id, running total)`. 222 events repeat the previous total (a refresh
re-emits the record), so the importer keys on the full total and collapses the
repeat. The result does not depend on file order because the key is a value.

**Token semantics.** `input_tokens` includes `cached_input_tokens` (cached never
exceeds input in 5,174 events). vibewatt stores `input - cached` as input and the
cached part as cache read. `output_tokens` includes `reasoning_output_tokens`
(reasoning never exceeds output), so reasoning is shown as thinking and not added
again. `cache_write_input_tokens` is 0 in every local event. `total_tokens` is
ignored: 119 events carry only a `total_tokens` baseline with every component 0.
These come from forked or subagent threads that inherit a parent total. They are
not responses.

**Model.** `turn_context.payload.model`, taken from the latest `turn_context`
before the event. 4 events precede any `turn_context`. They are counted as
`no_model` drops instead of being priced at a guess.

**Forks.** 25 rollouts have `forked_from_id` and 38 have `parent_thread_id`. None
of their running totals appear in the parent rollout, so a fork never double
counts its parent. The first `session_meta` in a file is the session's own.

**Resumed sessions.** A session resumed on another model can re-emit an old
running total right after its new `turn_context`. One local event did this: a
`gpt-5.4` context repeated a `gpt-5.5` total from three days earlier. The total
already identifies that response, so the repeat collapses into it and the
response keeps its original model.

**Identity.** Rollouts carry no account id. Codex keeps the signed-in ChatGPT
account in `$CODEX_HOME/auth.json` as `tokens.account_id`, the same value as the
id token's `chatgpt_account_id` claim. vibewatt reads only that field. Tokens are
never decoded, copied or stored. With keyring credential storage the file is
absent and the account is `unknown`. The account scopes Codex plan readings
(`chatgpt:<uuid>`). Usage rows still carry the install's Claude account and
machine identity, because a rollout does not record which ChatGPT account wrote
it.

**Plan readings.** Every `token_count` event repeats `rate_limits`: `primary`
(300 minutes on paid plans, 10,080 minutes on `free` and `go`) and `secondary`
(10,080 minutes) with `used_percent`, `resets_at` (epoch seconds) and
`plan_type`. Sync stores a reading only when a window's value, reset time or
plan changed, keyed by window length (`codex_five_hour`, `codex_seven_day`) and
labelled with the plan, for example "Codex weekly (plus)". The real rollouts gave
2,750 rows. Anthropic plan readers (meters, forecasts, peak findings, quota
history) exclude the `chatgpt:` scope, so a ChatGPT limit never shows as Claude
utilization. They describe the Codex plan of one ChatGPT account, not the Claude
account.

**Not observed.** Fast or priority tier, long prompts over 272K and non-zero cache
writes appear nowhere in the local data. The importer never sets `fast`. The 272K
rule below is implemented from the model pages and is covered by unit tests only.

### Codex rates (retrieved 2026-10-05)

Rates are USD per million tokens. Sources: `developers.openai.com/api/docs/models/<id>`
and the API changelog `developers.openai.com/api/docs/changelog`.

| Model | Input | Cache write | Cache read | Output | From |
|---|---|---|---|---|---|
| `gpt-5.4` | 2.50 | 2.50 (not billed separately) | 0.25 | 15 | 2026-03-05 |
| `gpt-5.5` | 5 | 5 (not billed separately) | 0.50 | 30 | 2026-04-24 |
| `gpt-5.6-terra` | 2 | 2.50 | 0.20 | 12 | 2026-07-30 |
| `gpt-5.6-sol` | 4 | 5 | 0.40 | 20 | 2026-08-21 to 2026-11-21 |
| `gpt-6-astra` | 10 | 12.50 | 1.00 | 50 | 2026-09-03 |
| `gpt-6.1-sol` | 2 | 2.50 | 0.10 | 10 | 2026-09-29 |
| `codex-auto-review` | as `gpt-5.4` | | | | 2026-04-30 |

`codex-auto-review` is the slug Codex stamps on approval-review ("guardian")
turns. It has no API page. OpenAI's auto-review report
([alignment.openai.com/auto-review](https://alignment.openai.com/auto-review/),
published 2026-04-30) names the reviewer GPT-5.4 Thinking at low reasoning, so
it is priced at `gpt-5.4` rates from that date. OpenAI has not published a
change of reviewer model since.

A prompt over 272,000 input tokens reprices the whole request at 2x input and
cache rates and 1.5x output. The model pages state this for every model above
except `gpt-5.6-sol`, which lists long-context rates without a threshold. A large
Sol prompt is flagged as an unverified premium.

Still unpriced on purpose: `gpt-5.6-terra` before 2026-07-30 and `gpt-5.6-sol`
before 2026-08-21. The changelog gives those older input and output prices but
no cache rates. ChatGPT plans bill by subscription, so every Codex cost is an
API-equivalent estimate, never a bill.

**Retention.** Codex keeps rollouts until the user removes them. Archived
sessions stay on disk. The importer keeps usage after a rollout disappears, the
same as for Claude.

**Scopes.** `all` is every provider, with a per-source split. `claude` is every
Claude surface (Claude Code, Cowork, web). Features that model Anthropic's plan
or Claude behaviour stay Claude-only under any scope and say so: findings,
context nudges, alert baselines, the 5-hour block and the plan-price comparison.
With `codex` selected the plan comparison is omitted.

**Gate evidence (2026-10-05).** A sync of the real rollouts into a copy of the
real store read 195 files and stored 4,828 Codex responses. An independent
recount of unique non-zero responses gave 4,829. The one difference is an event
with only `total_tokens` set. Input plus cached (540,632,017), cached
(513,104,000) and output (1,695,449) match the recount exactly. With the added
rates no Codex response is unpriced: $426.37 in total, of which
`codex-auto-review` is $32.49. Before the scope change, on the same store, the
Claude-only default and Codex added back to the old blended `all` exactly
(13,050 and $3,417.38 plus 4,828 and $393.85 equal 17,878 and $3,811.24).


## Copilot (Phase 10, observed 2026-10-05)

Source: VS Code's chat-session store under each `User` folder of Code and Code
Insiders (`%APPDATA%`, `~/Library/Application Support` or `~/.config`):
`workspaceStorage/<hash>/chatSessions/*.jsonl` and
`globalStorage/emptyWindowChatSessions/*.jsonl`. `VIBEWATT_VSCODE_USER_DIRS`
overrides the folders. Checked against 49 local sessions from VS Code Insiders,
2026-05-02 to 2026-09-15. Format notes from other trackers that read the same
store agree: codeburn (getagentseal/codeburn#563) and tokscale
(junhoyeo/tokscale#875).

**Journal.** A `.jsonl` session is a change journal. `kind 0` is a snapshot,
`kind 1` sets the value at key path `k`, `kind 2` appends to the array at `k`.
The importer replays it. An entry that does not fit the replayed state is
counted as `bad_journal_entry` and skipped; `__proto__`, `prototype` and
`constructor` path segments are refused. A plain `.json` snapshot is read as the
final state. When both forms of one session exist the journal wins.

**Response identity.** One request is one response: `requestId` plus
`responseId`. Dedup keeps the per-field maximum, so a re-emitted request counts
once in any order.

**Counts.** Output is `completionTokens`, the request total across every
tool-call round (`result.metadata.outputTokens` is the last round only and is a
fallback). Input is `promptTokens` (request, then `result.metadata`), which is
the last round's prompt only. Copilot does not log the input of earlier rounds,
so input is a floor for agent requests. The journal has no cache fields: cache
reads and writes show as 0.

**Model.** `result.metadata.resolvedModel`, the model that answered. The
request's `modelId` is usually the router `copilot/auto`; a request with only
the router id is a `no_model` drop. OpenAI dated snapshots such as
`gpt-5.4-mini-2026-03-17` normalize to their family id.

**Cost.** Newer requests carry `copilotCredits`, the amount billed for the whole
request (1 AI credit = $0.01). That amount is stored as `billed_usd` (schema 13)
and is the cost; a repricing never replaces it. On real data one 31-round
request billed 12.39 credits where its recorded tokens price to about 6.5, which
is why the billed figure wins. Requests without credits get an estimate from the
model rate. That estimate undercounts for the reason above.

**Rates.** Models seen only through Copilot use GitHub's per-token price list
([models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing),
retrieved 2026-10-05): `mai-code-1.1-flash` $0.20 / $0.02 cached / $1.20,
`gpt-5.4-mini` $0.75 / $0.075 / $4.50, `gpt-5.3-codex` $1.75 / $0.175 / $14.
GitHub moved monthly plans to per-token billing on 2026-06-01 (annual Pro and
Pro+ plans stay on premium requests until they renew), so these rates start
then. Claude models answered through Copilot use the Anthropic rates, which
match GitHub's table for the models seen.

**Plan readings.** Copilot caches each signed-in account's entitlement in
`%LOCALAPPDATA%/copilot/copilot-user-cache.json` (`// ` comment lines, then
JSON; `VIBEWATT_COPILOT_CACHE` overrides the path). Only `login`,
`copilot_plan`, `quota_snapshots.premium_interactions` and
`quota_reset_date_utc` are read. Each reading becomes `copilot_premium` under
the scope `github:<login>`, utilization `100 - percent_remaining`. Unlimited
snapshots are skipped. Anthropic and Codex plan readers exclude the `github:`
scope. The cache path is observed on Windows only; elsewhere it is a guess and
an absent file gives no readings.

**Identity.** The GitHub login scopes plan readings. Usage rows carry the
install's Claude account and machine identity; a session file does not name its
GitHub account.

**Not imported.** Copilot CLI (`~/.copilot/session-state`) holds no token fields
locally. Inline completions are not billed and not logged with counts.

**Gate evidence (2026-10-05).** A sync of the real store copy read 470 files and
stored 29 Copilot responses: Haiku 4.5 (16, $1.05 estimated), `gpt-5.4-mini`
(7, unpriced: May 2026, before per-token billing, with no input counts),
`mai-code-1.1-flash` (5, $0.23 billed) and `gpt-5.3-codex` (1, $0.26 billed).
Drops: 2 `no_usage`, 1 `no_model`. Plan readings: two GitHub logins.
