# Safe provider fixtures

Provider fixtures must be safe to publish **and** preserve the relationships
that make parsing and deduplication testable. Do not upload a real log and do
not rely on changing only a project or conversation title. Build a small,
wholly synthetic example instead.

## Keep real logs local

Use a real log only to learn the provider's record shape. Keep it on your
machine, copy the minimum fields into a new file, and replace every value with
invented data. Inspect the finished fixture line by line before committing it.
There is no automatic upload step and this guide is not a general-purpose
redaction tool.

Remove or replace all of the following, including copies nested inside metadata:

- prompts, responses, reasoning and tool output;
- URLs, hostnames, repository names, usernames and email addresses;
- home directories, workspace paths, document titles and branch names;
- access tokens, API keys, cookies, authorization headers and credential IDs;
- provider-specific metadata that could identify a person, account, device,
  organization or conversation.

If a field is not needed to exercise the parser, omit it. Never publish a
secret-looking value merely because it is expired. Search the final file for
real names, domains, path fragments and credential prefixes before uploading.

## Preserve relationships, not identities

Replace identifiers consistently. A repeated response must keep the same
synthetic message and request IDs on every streamed record; a different
response must use different IDs. Preserve timestamp ordering and the numeric
usage counters needed by the test. Do not randomly replace each occurrence.

This example is entirely invented:

| Field | Unsafe-shaped local value | Publishable synthetic value | Why it stays related |
| --- | --- | --- | --- |
| Session | `session-6f9…` | `fixture-session-1` | Same across the example |
| Request | `req-4b2…` | `fixture-request-1` | Same on placeholder and final records |
| Message | `msg-a17…` | `fixture-message-1` | Same response identity for deduplication |
| Workspace | `/Users/alex/acme-secret` | `/work/synthetic-project` | Path shape remains plausible, identity is invented |
| Prompt | A real customer request | `Summarize the synthetic report.` | No real conversation is retained |
| Tool output | A private file listing | `synthetic-report.txt` | Parser shape remains, content is invented |
| URL | An internal service URL | `https://example.invalid/report` | Reserved domain cannot identify a service |
| Credential | A real bearer token | Omit the field | Fixtures never need credentials |

The checked-in
[`sanitized_provider_example.jsonl`](../tests/fixtures/sanitized_provider_example.jsonl)
shows the resulting records. Its first two lines represent one streamed
response. They intentionally share `fixture-message-1` and
`fixture-request-1`; the later line raises `output_tokens` from `2` to `8`.
Deduplication must therefore retain one response with the per-field maximum.
The third line is a distinct response.

## Record the source contract

In the provider PR, record enough context for maintainers to reproduce the
shape without receiving your logs:

- source application and exact version;
- operating system and version;
- file format and, when the provider exposes it, format/schema version;
- whether each usage counter is per response or cumulative;
- the number of parsed responses before and after deduplication;
- expected deduplicated totals for every counter asserted by the test.

For the synthetic fixture in this repository, the source is documented as
`Synthetic Agent 1.2.3`, Linux, JSONL format version 1. The counters are per
response. Three records parse into three turns, then deduplicate to two
responses with totals of 15 input tokens and 12 output tokens; one duplicate is
collapsed.

## Verify before opening a PR

Add a focused test that runs the real parser and deduplication path. The test
for the example above is:

```bash
uv run pytest -q tests/test_provider_fixture_guide.py
```

Then run the full checks from [CONTRIBUTING.md](../CONTRIBUTING.md). In the PR
description, state the source application/version, OS, format version, counter
semantics and expected deduplicated totals. Confirm that the submitted file is
synthetic and that you performed a final manual inspection.
