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

When adding another usage provider, follow the [provider integration checklist](docs/PROVIDER-INTEGRATION.md) so source discovery, response identity, reporting, filters and archive handling are reviewed together.

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

## Try the dashboard with synthetic data

No agent account, subscription, credentials or personal logs are needed for this
route. Use a clean checkout and a shell without personal `VIBEWATT_CONFIG`,
`CCBURN_CONFIG`, `CODEX_HOME`, `VIBEWATT_VSCODE_USER_DIRS`,
`VIBEWATT_COPILOT_CACHE` or `ANTIGRAVITY_DATA_DIR` overrides: the current demo
isolation helper does not clear those inherited variables. After the setup above,
run these commands from the repository root:

```bash
cd web
npm run build
cd ..
uv run python scripts/demo_server.py
```

Open <http://127.0.0.1:8779> in your browser. Keep the command running while you
explore the dashboard; press **Ctrl+C** in that terminal to stop it. To use a
different port, pass it as the only argument, for example
`uv run python scripts/demo_server.py 8780`, and open that port instead.

The demo generates six weeks of made-up **Claude Code** transcripts. It isolates
the standard configuration/provider paths and the SQLite store in a new temporary directory,
turns on offline mode and disables quota fetching. Its displayed usage is
synthetic, not activity from your accounts. Each launch seeds a fresh temporary
store; the script does not remove that directory on exit. Do not use the demo
store to retain real usage history.

These servers have different purposes:

| Command (from the repository root) | Default address | Data |
| --- | --- | --- |
| `uv run python scripts/demo_server.py` | `http://127.0.0.1:8779` | Synthetic contributor demo |
| `uv run python web/e2e/fixture_server.py` | `http://127.0.0.1:8778` | Isolated end-to-end test fixtures |
| `uv run vibewatt serve` | `http://127.0.0.1:8777` | Your local usage store; ordinary startup can sync your logs |

Use the demo command for this quickstart. The fixture server belongs to browser
tests; the normal `serve` command is for your own usage data.

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

### Supported Python compatibility gate

The required Linux baseline remains Python 3.11. CI additionally runs the
offline Python pytest suite and installed-project import/CLI-help smokes
on stable CPython 3.12, 3.13, and 3.14. Frontend and platform checks stay
on the existing baseline; skipped code jobs still count as success through
the unchanged `ci-ok` aggregator. Update the extra matrix only when a
Python release is stable and dependency support has been independently checked.
Do not advertise support for a version whose hosted checks fail.

Per-run times are recorded by the GitHub Actions `python-compat` jobs;
the duration of this change's first run is **pending hosted CI**, not
an independently measured local result.

## Making a change

- **Bug fixes start with a failing test** that reproduces the bug. Fixtures go in
  `tests/fixtures/`.
- **Provider fixtures are wholly synthetic.** Follow the
  [safe provider-fixture guide](docs/PROVIDER-FIXTURES.md); real logs stay local.
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
