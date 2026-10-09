# Contributing to vibewatt

Thanks for helping. vibewatt is small. Its value is that its numbers are right,
so most of this guide is about keeping them right.

## Before you start

- Read [AGENTS.md](AGENTS.md). Its "Non-negotiables" section lists the rules that
  produce wrong numbers when broken. They apply to people as much as to agents.
- For anything bigger than a bug fix, open an issue first so we can agree on the
  approach. [docs/PLAN.md](docs/PLAN.md) is the spec and shows what is planned.

## Your first contribution

Issues labelled
[good first issue](https://github.com/MMALI3287/vibewatt/labels/good%20first%20issue)
are scoped for someone new to the code. Each one names the files to touch and
the acceptance criteria. Comment on the issue before you start so two people do
not build the same thing. Questions go to
[Discussions](https://github.com/MMALI3287/vibewatt/discussions).

**AI-assisted contributions are welcome.** You are responsible for every line:
review it, run the checks and be ready to explain it. Pull requests that look
generated and unreviewed, or that skip the template, are closed without review.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.11+ and Node.js 22.12+ (CI uses Node 24).

```bash
git clone https://github.com/MMALI3287/vibewatt.git
cd vibewatt
uv sync                     # Python package and dev tools
cd web && npm ci && cd ..   # frontend
```

Build once with `npm run build` in `web/`. Run the dashboard against your own logs with `uv run vibewatt serve`, or develop
the frontend with `cd web && npm run dev` next to it.

## Checks

Every PR must pass these.

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
cd web
npm run build && npm run test
npx playwright install chromium   # once
npx playwright test
```

Tests never read your real `~/.claude` and never touch the network. If you change
the API, run `npm run gen:api` in `web/` and commit `web/openapi.json` and
`web/src/api/schema.d.ts`; a test fails when they drift.

### No-network test contract

The Python suite rejects outbound socket and DNS calls before they reach the
network. A failure names the test and reports `external network blocked`; do not
disable the guard or add an allow-network marker. Replace provider and client
requests with synthetic fixture responses, patching the narrow request method at
the service boundary. Loopback traffic is reserved for tests that genuinely need
an in-process server or subprocess listener.

## Making a change

- **Bug fixes start with a failing test** that reproduces the bug. Fixtures go in
  `tests/fixtures/`.
- **Removals are recorded.** If you delete a file, table, flag or dependency, say
  why in the same PR, under the relevant phase in `docs/PLAN.md`.
- **New dependencies need a reason** in the PR description. The standard library
  comes first.
- **Numbers that only cover local logs are labelled local-only.** Plan
  utilization is the only account-wide figure.

## Commits and pull requests

- Branch from `master`: `feat/<slug>`, `fix/<slug>`, `chore/<slug>` or `docs/<slug>`.
- Use [Conventional Commits](https://www.conventionalcommits.org/) subjects, for
  example `fix: keep the newest days in the daily chart`. The body explains why.
- Keep a PR to one change. Describe what you verified and how.

## Reporting problems

Open an issue with the output of `vibewatt doctor`, your OS and what you expected
to see. `--mask-projects` hides project names if you share a screenshot. Never
paste your `~/.claude/.credentials.json` or an API key.

## License

By contributing you agree that your contribution is licensed under the
[MIT License](LICENSE).

## Release verification

Run `uv build` after the frontend build. The build hook rejects missing hashed JS
or CSS and the wheel excludes source maps. `uv build` rebuilds the wheel from the
sdist, which must carry the same assets. CI installs that wheel in a fresh venv on
Windows and Linux and runs `scripts/check_wheel.py --browser` with Node absent
from the server PATH. The browser controller uses the build host's Node.

The dependency floor gate is `uv run --isolated --no-project --resolution
lowest-direct --with-editable . --with "pytest>=8" --with httpx2 python -m pytest -q`.
## Publishing a release

1. Set the same version in `pyproject.toml`, `vibewatt/__init__.py` and
   `web/package.json` (`npm version X.Y.Z --no-git-tag-version`), then `uv lock`.
2. Add a `## X.Y.Z (date)` section to `CHANGELOG.md`.
3. Merge to `master`, then push the tag `vX.Y.Z` from that commit.

`.github/workflows/release.yml` refuses a tag that does not match all three
versions or has no changelog section. It builds and checks the wheel and sdist,
publishes them to PyPI through trusted publishing (no token is stored) and
creates the GitHub release with the changelog section and SHA-256 sums.

`scripts/demo_server.py` serves synthetic data for README screenshots.
