# Expensive runs: builds, test suites, benchmarks

Before repeating a slow build, test or benchmark run: plan the experiments, batch compatible ones, run targeted checks first, and keep exclusive resources (GPU) to one job.

Judgment, not a gate. A run of a few seconds needs none of this, and a risky or
foundational change deserves an immediate check before anything is stacked on it.

## Before the first slow run

- **Know the price.** Note what one build / suite / benchmark costs (wall time,
  GPU, tokens spent waiting) the first time you pay it. A step that costs minutes
  is worth a moment of planning; a step that costs seconds is not.
- **Plan the experiments, then run.** When several ideas are open, list them and
  prepare the compatible ones together, so one build and one run answers many
  questions, instead of edit → build → test once per idea.
- **Keep each result attributable.** Batch only what can still be judged alone:
  a flag, config value or parameter per variant, one commit per change, or
  separate cases in one run. Record the baseline once and reuse it. If a batch
  fails and you cannot tell which change did it, split it: that is the signal
  the batch was too wide, not a reason to stop batching.

## While iterating

- **Narrowest check that can fail for the right reason:** the one test, module
  or incremental target covering the edit. Run the broader suite at checkpoints:
  before a commit that others build on, before handing off or reporting done,
  after a merge or a change to shared code.
- **Do not pay twice.** No rebuild when no input changed; no re-run of a passing
  suite "to be sure"; read the log or artifact from the last run before starting
  another. Reuse build caches and incremental modes the project already has.
- **Fail early.** Order cheap checks (lint, type check, compile, one smoke case)
  ahead of the long run, and stop a run whose result you already know.

## Shared and exclusive resources

- A benchmark or anything else that needs the **whole GPU** (or a device, a
  port, a licensed tool) runs alone: two at once make both numbers meaningless.
  Check what is already running before you start (`python -m api.jobs work-tree`,
  `nvidia-smi`), and wait or do other work rather than overlap.
- Sibling agents that share the resource serialize on one lock, for example
  `flock {project}/temp/gpu.lock <command>`; a project's own runner or queue
  wins when it has one. Never stop another chat's job to free the resource.
- CPU-only work (edits, unit tests, analysis of the last result) can proceed
  while an exclusive job runs.
- Long runs follow [headless-turns.md](headless-turns.md) (watch card or
  `watch-unit`, no wait loops); a batch split across agents follows
  [subagents.md](subagents.md).

## Reporting

Say what was run and what was not: which targeted checks passed, whether the
broad suite ran, and which variants a batched run covered.
