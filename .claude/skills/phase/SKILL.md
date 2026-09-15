---
name: phase
description: Run one numbered phase of PLAN.md end to end - explore, plan, implement, gate, review, commit
disable-model-invocation: true
---
Execute phase $ARGUMENTS of `PLAN.md`.

Work in this order and do not skip a step.

1. Read `PLAN.md` section 8 for this phase, plus section 2 (data sources), section
   10 (traps) and `CLAUDE.md`. Read every file the phase names. Use a subagent for
   any wider codebase investigation so it does not fill this context.

2. Before writing code, state:
   - the files you will add or change
   - the interfaces you will introduce
   - which traps in section 10 this phase could hit
   - anything in the phase you believe is wrong or underspecified

   Stop and wait for approval.

3. Implement only what the phase lists. Anything you notice outside its scope goes
   in the "Deferred" section at the bottom of `PLAN.md`, not into the diff.

4. Run the phase's Gate command. Paste the real output. If it fails, fix the root
   cause rather than the symptom, and never weaken the Gate to make it pass.

5. Launch the `plan-reviewer` subagent against the diff. Fix every correctness gap
   it reports. Ignore style preferences.

6. Commit on a `feat/phase-N-<slug>` branch with a Conventional Commit subject and
   a body explaining why. Push to `master` only through a PR.

Report at the end: what shipped, the Gate output, what the reviewer found, and
anything you deferred.
