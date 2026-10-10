# Store snapshots for agents

```sh
vibewatt status --json
vibewatt quota --json
vibewatt status --json --source claude-code --days 7
vibewatt status --json --out status.json
```

These commands read retained data. They never sync transcripts, refresh pricing,
query the cloud session API or fetch quota. Run `vibewatt sync` separately to
update local usage. Quota is supplied by the existing statusline/background
collection paths. Creating/opening the local store can perform its existing
initialization; the commands are not a live collector.

Both commands emit schema version 1 with these stable top-level fields:

| Field | Meaning |
|---|---|
| `schema_version` | Integer contract version, currently `1`. |
| `command` | `status` or `quota`. |
| `generated_at` | Snapshot generation time in UTC, not evidence freshness. |
| `state` | `available`, `partial`, `unavailable` or `error`. |
| `usage` | Local retained usage and provenance for `status`; `null` for `quota`. |
| `quota` | Retained current windows with source, scope, reset and sample metadata. |
| `error` | `null` or an object with `code` and `message`. |

`usage.coverage` is `retained_local`. It excludes harvested cloud totals and is
never account-wide. Cost is an API-equivalent estimate. `last_sync_at`/`as_of`
describe source freshness independently of `generated_at` and the selected
report date. Unknown models remain explicit in the report.

Quota availability means a usable retained sample exists under existing quota
expiry rules. It does not mean the account was fetched at command invocation.
The timestamp is the sample time. Inspect it and the source before treating
whole-percent desktop readings as current. Forecasts are not official readings.

| Exit | State | Meaning |
|---|---|---|
| 0 | `available` | Quota is available; for `status`, local usage and priced models are also available. |
| 1 | `partial` / `unavailable` | Usage or quota is missing, quota is disabled or models are unpriced. Inspect the payload rather than assuming zero usage. |
| 2 | `error` | A snapshot failed, such as a database read error. |

`resync_required` means the requested timezone or day-start hour differs from
the stored calendar buckets. Sync explicitly with that configuration before
requesting the snapshot. The command never silently rebuckets data. If an
`--out` file cannot be written, `output_failed` is emitted on stdout with exit 2.

Report and snapshot `--out` writes use a sibling temporary file and atomic
replacement. If writing or replacement fails (including a Windows file lock),
the existing destination bytes are preserved and the temporary file is removed.
JSON/CSV report commands return exit 2 and a readable `output_failed` error on
stderr; status/quota retain their structured JSON error on stdout. Successful
replacement keeps an existing regular file's permissions where supported; new
files use private mode 0600 on POSIX. An existing symlink is replaced, not
followed, leaving its referent untouched. Parent directories are not created.

`schema_too_new` means a newer vibewatt installation wrote the database. The
command exits 2 without opening it for application use, migrating it, or creating
a migration backup. Upgrade to a vibewatt version that supports the schema named
in the error and reopen the original store. Do not edit `meta.schema` to bypass
the check; changing the version number does not downgrade the format.

If you must use an older installation, stop vibewatt processes using the store
and preserve the original data directory, including any SQLite `-wal` and `-shm`
files. Restore a backup made by a compatible version into a **separate** data
directory, keeping its relative database path, and point `VIBEWATT_DATA_DIR` at
that directory. Pre-migration backups are named `*.pre-v<schema>-<timestamp>.bak`;
restore one under the original database filename in the separate directory.
The restored copy only contains data retained at backup time. Keep the newer
original for reopening with a compatible version; do not delete or overwrite it.

Invalid command arguments and invalid configuration resolved before snapshot
generation use normal CLI errors on stderr; those errors are not a JSON
snapshot. Consumers should check both exit status and whether stdout contains a
valid versioned object. Additive fields may appear within version 1; consumers
should ignore fields they do not recognize.
