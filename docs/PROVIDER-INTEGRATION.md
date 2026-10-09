# Adding a usage provider: integration checklist

A new provider is more than a parser plus prices. This guide traces the
existing **Claude Code local** path in the current repository. Use it with
[CONTRIBUTING.md](../CONTRIBUTING.md) and the [data-source evidence](DATA-SOURCES.md);
the governing behavioral contract is [AGENTS.md](../AGENTS.md).

This guide **does not** add a plugin API or promise support for a source whose
real output has not been observed. Gemini CLI and OpenCode remain tracked in
their own issues; do not infer a schema from another provider.

## Follow one existing provider end to end

| Stage | Existing Claude Code path | Checklist for a new source |
| --- | --- | --- |
| Source identity | `CLAUDE_CODE`, `CLAUDE_SOURCES`, `CLAUDE_SURFACES` and `source_clause()` in [sources.py](../vibewatt/sources.py) | Add a distinct source identifier; preserve `all` and `claude` scopes instead of silently mixing vendors. |
| Discovery | [ingest/claude_code.py](../vibewatt/ingest/claude_code.py) handles project roots and `CLAUDE_CONFIG_DIR`; [ingest/__init__.py](../vibewatt/ingest/__init__.py) registers the module in `SOURCES`. | Implement `discover(cfg)` and `parse(path, drops, raw)`; return billable `Turn` records without deduping inside the parser. A cloud-only feed needs a separately evidenced interface, not a fake local path. |
| Response identity | [sources.py](../vibewatt/sources.py) has `Turn`, `response_key()`, `dedupe()` and `merge()`. | Establish a stable response ID with a real fixture. Claude Code dedups by `(message.id, requestId)`; without request ID it includes session and timestamp. A new format needs its **own observed equivalent**, not a guessed ID. |
| Per-field counts | `TOKEN_FIELDS` and `merge()` keep each repeated response's field-wise maximum; main-thread evidence outranks a sidechain replay. | Preserve the provider's actual cumulative/per-response interpretation. Test partial streamed placeholders, duplicate content blocks, different file orders, missing IDs and cache buckets. Never sum repeated response totals. |
| Sync and SQLite | [store.py](../vibewatt/store.py) `sync_files()` checks file mtime **and** size, calls source parsing and `dedupe()`, then `upsert_turns()` and rollup rebuilding. [cli.py](../vibewatt/cli.py) `sync_store()` coordinates it. | Repeated sync, overlapping files and later stream updates must be idempotent and preserve historical totals. Report endpoints use `aggregate.from_store()`, not request-time log parsing. |
| Price attribution | [pricing.py](../vibewatt/pricing.py) `rate_for()` uses a known model identity, provider-specific rates and documented overrides. | Verify exact model IDs, supported dates and the provider's token/caching definitions. Keep 5-minute and 1-hour cache-write buckets separate where applicable. Unknown models remain **unpriced**, never default to zero or a sibling's rate. |
| CLI and reports | [cli.py](../vibewatt/cli.py) `build_parser()` defines `--source`; `build_report()` queries `aggregate.from_store()`. | Add the new name to the CLI choices and check exact source/project/model/date filters. The display must distinguish local-only usage from account-wide information. |
| HTTP API | [api/dependencies.py](../vibewatt/api/dependencies.py) `get_filters()` validates its `source: Literal[...]`; [api/routes.py](../vibewatt/api/routes.py) consumes it. | Extend the accepted source values consistently, retain validated shared filters, and regenerate `web/openapi.json` plus `web/src/api/schema.d.ts` using `cd web && npm run gen:api` **if the API contract changes**. |
| Dashboard | [web/src/lib/filters.ts](../web/src/lib/filters.ts) has the `Source` union, allowed `SOURCES`, URL serialization and query conversion. | Add the same source selection to all relevant frontend filter controls; verify URL reload and mobile behavior. Do not present a new provider as Claude. |
| Diagnostics | [doctor.py](../vibewatt/doctor.py) lists discovered source files and per-source coverage. | Make missing files, unsupported formats or unverified coverage visible without mislabeling missing observations as zero usage. |
| Archive transfer | [transfer.py](../vibewatt/transfer.py) `_validate()` limits `row["source"]` to known providers and validates version, account, response identity, timestamps and counters. `import_file()` merges by identity. | Update the source allowlist and the archive tests. Keep account boundaries, version validation and repeated import idempotency; never import unknown sources as `claude-code`. |
| Plan and quota | [quota.py](../vibewatt/quota.py) reads account-level samples separately from local turns; [api/routes.py](../vibewatt/api/routes.py) gates Anthropic plan comparison by source. | Scope quota readings to their verified provider/account. If the provider has no demonstrated quota source, report it as unavailable; never derive account-wide plan use from local logs. |

## Evidence before implementation

1. Record the **source application and version**, OS, log/format version, known
   storage locations and whether counters are cumulative or per response.
   Identify response IDs, replay cases, timestamps, token fields and which
   values are absent, not merely zero.
2. Keep real logs **local**. Submit a wholly synthetic, inspected fixture
   preserving the ID relationships and counters that make the behavior
   reproducible. Remove prompts, replies, tool outputs, usernames, paths,
   URLs, secrets and embedded metadata, not just titles.
3. Start with [tests/test_ingest.py](../tests/test_ingest.py), which proves
   each source exposes the same discovery/parse interface, and the existing
   [Claude fixture](../tests/fixtures/claude_code_session.jsonl). Anchor
   regression tests to [test_dedup_store.py](../tests/test_dedup_store.py):
   `test_placeholder_then_final_keeps_the_final_counts` and
   `test_placeholder_and_final_in_separate_syncs`.
4. Exercise the end-to-end path in a **clean, isolated store**: discover,
   parse, dedupe, sync, report, filter, export and import; verify that
   repeated sync/import does not increase counts. See
   [tests/test_transfer.py](../tests/test_transfer.py) for source/account
   validation and repeat-import coverage.
5. Independently check an **observed real sync** using consented data held
   locally. A synthetic fixture can prove parser mechanics but cannot prove
   the real provider's log contract or all billing/plan semantics. Record
   the exact app version, operating system, observed record counts,
   expected deduplicated response totals, unknown models, unsupported
   fields and the sanitized test evidence. Keep unverified surfaces out
   of the supported-source claim.

## Definition of done

- [ ] The provider is discoverable and emits bounded `Turn` records with
      tested response identity and idempotent deduplication across files.
- [ ] Per-field usage, cache/TTL and dated exact-model pricing match
      observed evidence; missing prices remain explicitly unpriced.
- [ ] CLI, API `source` Literal, frontend `Source` union and visible UI
      source choices agree; API schema files are regenerated if affected.
- [ ] Archive import/export validates the source and account and remains
      idempotent; local-only metrics and separate quota scopes are honest.
- [ ] Unit fixtures, integration/repeat-sync tests, a local real-sync
      verification receipt and the checks in
      [CONTRIBUTING.md](../CONTRIBUTING.md) support the claims.
- [ ] No new runtime dependency or plugin framework was introduced without
      a separately approved scope.

If evidence is insufficient, keep the provider **deferred** and document
that uncertainty in [DEFERRED.md](DEFERRED.md). This checklist is guidance,
not a claim that a new integration was tested or delivered.
