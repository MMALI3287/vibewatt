# Audit probes (2026-09-22)

Tests written during the phase 1-6 audit (`docs/AUDIT-2026-09-22.md`). Most
fail on `bcc937e` by design: each one reproduces a finding. Use them as the
starting failing test when fixing an A-item in Phase 6.5, then move the test
into `tests/` as a normal `test_*.py`.

Files are named `probe_*.py` so a plain `pytest` run never collects them, and
ruff excludes this directory. `conftest.py` here repeats the isolation fixture
from `tests/conftest.py`, so probes never read a real `~/.claude`. Run one
explicitly from the repo root:

```bash
uv run pytest -q docs/audit-2026-09-22/probes/probes_d1/probe_d1_dedup.py
```

Probes import the package as `ccburn`. Update the imports after the Phase 6.5a
rename. `web/` holds Playwright and vitest probes: copy one into `web/e2e/` or
`web/src/` to run it.

| Directory | Covers | Kind |
|---|---|---|
| `probes_d1/` | dimension d1 | auditor probes |
| `probes_d10/` | dimension d10 | auditor probes |
| `probes_d2/` | dimension d2 | auditor probes |
| `probes_d4/` | dimension d4 | auditor probes |
| `probes_d5/` | dimension d5 | auditor probes |
| `probes_d6/` | dimension d6 | auditor probes |
| `probes_d7/` | dimension d7 | auditor probes |
| `probes_d8/` | dimension d8 | auditor probes |
| `probes_d9/` | dimension d9 | auditor probes |
| `probes_v0_combined/` | A-001 | independent verifier repro |
| `probes_v123_combined/` | A-023 | independent verifier repro |
| `probes_v124_combined/` | A-024 | independent verifier repro |
| `probes_v13_combined/` | A-003 | independent verifier repro |
| `probes_v14_combined/` | A-025 | independent verifier repro |
| `probes_v18_combined/` | A-007 | independent verifier repro |
| `probes_v19_combined/` | A-004 | independent verifier repro |
| `probes_v1_combined/` | A-002 | independent verifier repro |
| `probes_v32_combined/` | A-041 | independent verifier repro |
| `probes_v43_combined/` | A-006 | independent verifier repro |
| `probes_v66_combined/` | A-014 | independent verifier repro |
| `probes_v67_combined/` | A-015 | independent verifier repro |
| `probes_v68_combined/` | A-016 | independent verifier repro |
| `probes_v69_combined/` | A-034 | independent verifier repro |
| `probes_v70_combined/` | A-052 | independent verifier repro |
| `web/` | frontend dimensions d3a, d3b, d9 | Playwright and vitest probes |
