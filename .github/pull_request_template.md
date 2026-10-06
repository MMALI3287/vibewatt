<!--
Thanks for contributing. PRs that leave this template empty or skip the checks
are closed without review. Features need an agreed issue first.
-->

## Why

<!-- The problem this solves. -->

Closes #

## What I verified

<!-- Commands you ran and what they showed. "Tests pass" alone is not enough. -->

## Checklist

- [ ] This PR does one thing and links an issue (required for features)
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run pytest -q`
- [ ] `cd web && npm run build && npm run test` (if the frontend changed)
- [ ] `npm run gen:api` with both files committed (if the API changed)
- [ ] Bug fixes start with a failing test
- [ ] Removals are recorded with a reason in `docs/PLAN.md`
- [ ] New dependencies are justified here
- [ ] No real logs, tokens, project names or prompt text in code, tests or screenshots
- [ ] If an AI tool wrote part of this, I reviewed every line and can explain it
