# How findings, alerts and Wrapped are computed

This is the reference for the analysis rules. `PLAN.md` section 7 holds the
specs they implement; this file says what the code does today and why.

## Coverage

- Findings, the reconciliation panel and Wrapped's `local_summary` read the local
  store. Web and Cowork remote sessions appear only as harvested session totals.
- Plan utilization, quota alerts and peak windows are account-wide.
- Costs are the prices stored at sync time. Harvested API costs are never
  recalculated. An unpriced model is reported as unpriced, never as $0.

## Findings

| Rule | Trigger |
|---|---|
| Daily anomaly | A day's cost exceeds `median + 3 × max(1.4826 × MAD, 0.1 × median)` of the previous 28 active, fully priced days. At least 14 are needed. Inactive days are not $0 days. A day with unpriced spend is skipped and names its models; it is left out of later baselines instead of blocking them. |
| Cache opportunity | More than 200k total input tokens (cache reads and writes included) and a cache-hit ratio below 50%. |
| Cache advice | Derived from a cache opportunity: stable instructions, cache configuration. |
| Model advice | At least five Opus responses averaging fewer than 500 output tokens. Suggests trying Sonnet with a quality comparison; never claims equivalence from output length. |
| Subagent overhead | At least five responses with 50% or more of tokens in sidechains. |
| Fast-mode advice | A positive fast-versus-standard price difference for the same tokens, or unknown pricing. |
| Repeated reads | Three or more distinct `Read` calls for one normalized path in a session. Replayed tool ids count once. An investigation signal: reads may cover different ranges or a changed file. |
| Cache-read to output | At least 200k cache-read tokens and more than 100 cache-read tokens per output token. |
| Long, low-output session | Five or more responses over two hours or more with fewer than 1,000 output tokens. Elapsed time includes idle gaps. |
| Peak window | Two or more reset windows reached 100% on different dates at the same weekday and hour. The finding names the time zone. |
| Context nudge | The latest harvested `context_used / context_max` is above 70% (warning) or 85% (urgent). |

**Savings** are upper-bound scenarios, not guarantees. They overlap: do not add
them. The cache saving prices uncached input and each TTL's cache writes as cache
reads, per response, with its model, fast-mode and region rates. That is more
than PLAN 7.6's formula (input only) for write-heavy sessions; the deviation is
recorded there.

**Snapshots.** `GET /api/findings` serves the stored snapshot for a filter
selection until the store or the day changes. `POST /api/analysis` recomputes.

**Dismissals** live in their own table. A session finding stays dismissed under
every filter; other findings stay dismissed for the selection they were
dismissed in.

## Repeated-read metadata and privacy

Sync reads only `Read` tool-use ids, session, time, source, project, model and a
hash of the normalized target path. The hash is an HMAC under a random key kept
in the store, so a guessed path cannot be confirmed from a copied store. No
literal path, tool argument, file content or prompt is stored. Paths are
normalized with the producer's Windows or POSIX rules, whatever the host OS.

## Alerts

- **Quota.** Each plan window reports a pace delta (used % minus elapsed %) and a
  P10-P90 projection at reset from how much this account's past windows of the
  same kind still grew after the same elapsed fraction. At least four past
  windows are needed; until then it says so. Resets are detected from the data.
  An alert belongs to one window episode and fires once: when the window reaches
  100%, or when the median projection does.
- **Spike.** A local response costing more than five times the median of the 50
  priced responses before it. Only the last 24 hours are checked.
- **Active block.** Local burn rate for the current 5-hour block. It is never
  converted into account-wide utilization.

Alerts are dashboard evidence, not desktop notifications or webhooks.

## Wrapped

- Days and the year use the report time zone and `day_start_hour`. Hour-of-day
  figures use clock hours.
- Busiest day and biggest session mean the most tokens. Cloud totals belong to
  their session's start day; no cloud hour can be inferred.
- The longest streak is an observed activity streak within the year.
- The plan multiple compares API-equivalent cost with twelve times the configured
  monthly price, not with actual payments.
- Cache savings are local cache-read tokens valued at input minus cache-read
  rate, with speed and region overrides. They do not estimate cloud savings.
- The share card holds aggregate figures only: no project names, no titles.

## Claude's Stats, compared

The desktop app and `/usage` Stats count every log line without dedup, so their
figures run 2-3x vibewatt's. `sources.stats_line()` is their rule: messages are
user and assistant lines outside subagents; tokens are the naive input + output of
every assistant line, subagents included; no cache; Claude Code only. It
reproduced one desktop snapshot exactly (33 sessions, 21,183 messages, 14,964,413
tokens). The Overview shows this figure beside vibewatt's, labelled, never as a
headline. `scripts/reconcile_stats.py --until <time>` checks a snapshot to the
second.

A vibewatt **session** is a session id with at least one billable response kept
after dedup.

## Bounded external work

- **AI weekly summary.** Off by default. With `ai_summary.enabled` and
  `ANTHROPIC_API_KEY`, it sends numeric aggregates, date labels and a session
  count to the Messages API. Titles, prompts, session ids and model labels are
  never sent; project names only with `include_project_names`. Request and
  response are capped at 64 KiB, output at 600 tokens, with a 15-second timeout.
- **Service status.** `status.claude.com/api/v2/summary.json`, a 3-second total
  deadline, a 512 KiB cap, cached for five minutes. The page never waits for it.
- **Resume brief.** A project directory needs an explicit `project_paths` entry.
  Git runs without a shell, with optional locks and fsmonitor off, capped at
  64 KiB and three seconds. `TODO.md` must sit inside the mapped directory, may
  not be a symlink and is capped at 64 KiB.

## Local context and activity (Phase 8)

Overview shows local context nudges from the latest main-thread response of each
session active within 30 minutes. Input plus all cache reads/writes forms the
prompt size. It reuses the cloud thresholds: above 70% warning and above 85%
urgent. Capacity is conservatively 200K unless the model id includes `[1m]` or a
prior main-thread response in that session exceeded 200K. Unknown models receive
no guessed denominator. Quota and sidechain activity never affect the nudge.
The API reads stored responses at request time so idle sessions expire.

Activity is separately derived from retained history timestamps and projects.
Its calendar and streak use the report timezone and day-start setting. The panel
shows the 91 days ending at the latest activity date; streaks use all retained
activity. It deliberately ignores usage filters because history has no reliable
model/token information. It contributes no tokens or cost.
