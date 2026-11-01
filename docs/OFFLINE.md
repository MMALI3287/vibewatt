# Offline mode: a command-by-command checklist

vibewatt reads local logs by default and reaches the network only for pricing,
plan utilization, service status and the opt-in AI summary. This page is the
order to turn each of those off, what each command proves, and the two places
where the flags do more than their one-line help suggests.

The authoritative list of outbound calls is the table in
[DATA-SOURCES.md](DATA-SOURCES.md#outbound-calls). This page is the runnable
form of it.

## The two switches

| Flag | What it sets | What it stops |
| --- | --- | --- |
| `--offline` | `offline: true` | Pricing refresh, the status banner, and the live plan-utilization fetch on the fallback path |
| `--no-quota` | `quota: false` | The account-level plan-utilization lookup, and the quota series in the reading |

Neither touches reading of local logs, and neither is a privacy switch: they
control the four calls in the table above, not whether local files are read.
That is stated in [CONFIGURATION.md](CONFIGURATION.md#nested-objects-are-replaced-not-merged)
and repeated here because "offline" is easy to read as "isolated".

Both are one-way file overrides, like every boolean CLI switch: omitting the
flag does **not** clear a `true` already in a config file. If a file has
`"offline": true`, running without `--offline` stays offline; to fetch again
you must edit that file to `"offline": false` — there is no `--no-offline` to
turn it off.

## Checklist

Run these in order. Each step names what a pass looks like, using `--no-color`
so the output is diffable.

### 1. Prove pricing refresh is skipped

```sh
vibewatt report --offline --no-color
```

Pass: it prints a report and makes no request to
`raw.githubusercontent.com/BerriAI/litellm/...`. The pricing table is taken
from `BUILTIN` and any local cache. A cold cache still prints a report — the
built-in table is the source of record, not a fallback.

### 2. Prove the account-level lookup is skipped

```sh
vibewatt report --offline --no-quota --no-color
```

Pass: it prints a report, and the plan-utilization figure is absent rather than
stale. With `quota: false`, `quota.read()` returns `(None, "disabled in config")`
before any request is considered.

### 3. Confirm the status panel is not fetched

`status`/`quota` use their own path, without the report's log sync and pricing
refresh ([CONFIGURATION.md](CONFIGURATION.md#defaults-and-limits)). The status
banner on the dashboard is skipped when `offline` is true.

```sh
vibewatt status --offline --no-quota
```

Pass: it prints the stored reading, no banner is fetched from
`status.claude.com`.

### 4. Confirm `sync` does not fetch pricing

`sync` refreshes pricing itself, before it reads any log —
`sync()` calls `pricing.refresh(offline=cfg.get("offline", False))` before it
discovers files. The `--offline` you passed to `report` in step 1 lives only in
that command's config, so it does **not** reach a separate `sync` invocation.
Pass the flag to `sync` itself:

```sh
vibewatt sync --offline
```

Pass: it prints its read progress and makes no pricing request. Run plain
`vibewatt sync` and it may refresh the table regardless of any earlier
`report --offline`.

`sync` reads the configured log roots and writes SQLite; it makes no status or
quota request.

### 5. Confirm the dashboard inherits the flags

```sh
vibewatt serve --offline --no-quota
```

Pass: the served dashboard shows no service-status banner and no plan
utilization, and makes none of the four calls. `serve` reads `offline`/`quota`
from the same config the CLI writes, so `--offline --no-quota` on the `serve`
command line are the switches that matter.

### 6. Keep the AI summary off

The weekly summary is off by default (`ai_summary.enabled: false`) and only
fires on a button click. Leave it false for an offline run; it is a POST to
`api.anthropic.com/v1/messages` and is the only call that sends prompt-adjacent
text.

## What an offline run can and cannot show

Offline does not mean blank. The store is the source of truth for everything a
report prints, and it survives an offline run unchanged.

- **A report reads stored rollups, refreshed from local logs.** `report` prints the store's aggregate rows. It also runs a sync first: `report` calls `build_report(..., refresh=True)`, which discovers the local log files and syncs the changed ones into the store before querying the rollups. No network is involved in that refresh - it reads files the local machine already has - so `report --offline` still syncs changed local logs, and it is not limited to what a previous explicit `sync` stored.
- **An empty store fills from local logs, not from a prior sync.** Because `report` syncs changed local logs itself, `report --offline` populates a fresh, empty store from whatever local logs exist, with no separate `vibewatt sync` first. The exception is a machine with **no local logs**: then there is nothing to sync and nothing to show - repeating `sync` cannot create usage out of absent source data, and an offline run cannot invent it. Empty store (no rows yet) and absent source data (no logs) are different states: the first is fixed by any command that syncs, the second by producing logs.
- **Prices can be stale.** With `--offline` the report uses the built-in table
  and the last cached community table. A model priced only in the community
  table and never fetched on this machine is unpriced here, and `doctor` warns
  when the cached table is more than 7 days old. That is the intended trade for
  making no request.
- **Quota mixes a local series with one network call.** Plan utilization is read from local samples first: `vibewatt statusline` (a hook that pipes Claude Code's statusline JSON) and the desktop app's `plan-usage-history.json` feed the same `quota_samples` series without any request, so `--offline` can still show a plan figure from those local samples, subject to their freshness (`latest()` drops a window whose reset has passed or whose sample is older than its window length). `--offline` only stops the outbound `api.anthropic.com/api/oauth/usage` fetch on the store-unavailable fallback. `--no-quota` is the stronger switch: `quota.read()` returns `(None, "disabled in config")` before any source is read, so no stored series is shown either. Everything else - token counts, costs, activity, context - is local and unaffected.
- **The scope stays local.** Nothing above reads another machine or another
  account. Offline is about the four calls in the table, not about reach.

### A fully synthetic run, with no personal data

To exercise all of this without touching real logs, `scripts/demo_server.py` is the route that isolates everything: it seeds synthetic transcripts into a temporary directory, points the standard config and provider paths and the SQLite store at that directory (`fixture_server.isolate`), turns on offline mode and disables quota fetching, then serves the dashboard from the synthetic store ([CONTRIBUTING.md](../CONTRIBUTING.md)).

```sh
uv run python scripts/demo_server.py     # serves http://127.0.0.1:8779
```

Two limits keep this honest:

- `VIBEWATT_DATA_DIR` alone is **not** a fully synthetic run. It changes where the store is written, but the sync still discovers and reads the user's real Claude/Cowork/Codex logs and writes their usage into the throwaway store. Setting only `VIBEWATT_DATA_DIR` and running `vibewatt sync` therefore does **not** prove offline mode is free of personal data - it just puts the output somewhere else. Use `scripts/demo_server.py`, which also redirects the provider and config paths, for a run with no personal data.
- The demo isolation helper sets the standard paths but does not clear every inherited override. Per [CONTRIBUTING.md](../CONTRIBUTING.md), start it from a shell without personal `VIBEWATT_CONFIG`, `CCBURN_CONFIG`, `CODEX_HOME`, `VIBEWATT_VSCODE_USER_DIRS`, `VIBEWATT_COPILOT_CACHE` or `ANTIGRAVITY_DATA_DIR` variables.

The demo seeds its own transcripts, so it does not depend on local logs at all; its data is synthetic and reproducible.

## Where the one-line help is narrower than the behaviour

Two flags do more than their `--help` text says. Both are worth knowing before
you rely on the checklist:

- `--offline`'s help says "never fetch pricing". In
  [`quota.read()`](../vibewatt/quota.py) the live plan-utilization fetch on the
  store-unavailable fallback is also gated on `not config.get("offline")`, so
  `--offline` stops that request too. `--no-quota` is the documented switch for
  it, but `--offline` has the same effect on the fallback path.
- `--no-quota`'s help says "skip the account-level lookup". It also removes the
  quota **series** from the reading, not just the one request, so a stored
  history is not shown while it is set.

## What is not covered

`--offline` and `--no-quota` do not sandbox the machine. Local log reads,
SQLite writes and the archive import/export paths are unaffected. For a
synthetic dashboard with no personal data at all, use `scripts/demo_server.py`
as [CONTRIBUTING.md](../CONTRIBUTING.md) describes, rather than these flags.
