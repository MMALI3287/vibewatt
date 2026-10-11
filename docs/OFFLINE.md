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
flag does **not** turn off a `true` in a config file. To turn `offline` back on
after it is in a file you must edit the file — there is no `--no-offline`.

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

### 4. Confirm `sync` still only reads local files

```sh
vibewatt sync
```

`sync` reads the configured log roots and SQLite; it does not fetch pricing or
status on its own. Pricing is loaded (and refreshed) at report/`serve` startup,
not during the file walk, so step 1 is what governs the request.

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
