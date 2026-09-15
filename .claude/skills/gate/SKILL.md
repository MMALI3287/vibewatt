---
name: gate
description: Run every quality gate - lint, types, tests, build, overflow check - and report real output
disable-model-invocation: true
---
Run the full gate and report actual output, not a summary of it.

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
cd web && npm run build && npm run test && cd ..
```

Then confirm the invariants that regress most often:

- `ccburn sync` twice in a row leaves the store totals unchanged (idempotent).
- `ccburn doctor` reports coverage without raising.
- The dashboard has zero horizontal overflow at 1440, 1024, 768 and 390px.
- A filtered URL reloads into the same view.

For anything failing, give the failing command, its output, and the root cause.
Do not report success for a step you did not run.
