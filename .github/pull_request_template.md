## Why

<!-- The problem this solves. Link the issue if there is one. -->

## What I verified

<!-- Commands you ran and what they showed. -->

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run pytest -q`
- [ ] `cd web && npm run build && npm run test` (if the frontend changed)
- [ ] `npm run gen:api` with both files committed (if the API changed)
- [ ] Bug fixes start with a failing test
- [ ] Removals are recorded with a reason in `docs/PLAN.md`
- [ ] New dependencies are justified here
