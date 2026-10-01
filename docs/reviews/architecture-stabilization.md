# Architecture stabilization — outcome and validation

The architecture stabilization initiative (Phases 0–7) completed on
2026-10-01. The final code-bearing commit was `a3311fcf`; the original
closeout was recorded in `7822a644`.

The initiative separated domain ownership from Flask routes and browser
page orchestration, then added checks for those boundaries. The
[stabilization plan](../architecture/ARCHITECTURE_STABILIZATION_PLAN.md)
records the scope and completed phases. The
[repository map](../architecture/repository-map.md) and
[extension boundaries](../architecture/extension-boundaries.md) describe
where new work belongs.

## Completed work

- **Phase 0:** measured the architecture baseline and mapped ownership.
- **Phase 1:** separated Spaces logic from shell orchestration.
- **Phase 2:** extracted projects, action forms, Git, and tasks HTTP
  boundaries into owned services and route modules.
- **Phase 3:** separated chat project selection, slash commands, action
  forms, activity, attachments, composer, agent/model controls, messages,
  rendering, follow-up queues, and turn lifecycle from page orchestration.
- **Phase 4:** established shared backend owners for live status, status
  queues, metadata, and project context.
- **Phase 5:** introduced shared chat-turn submission for synchronous,
  streamed, and pipeline fallback execution. Follow-up corrections restored
  stale-turn completion ordering and ownership of the no-LLM fallback.
- **Phase 6:** retired executable vendor CLI installation machinery while
  retaining BYO-CLI guidance; hardened opt-in project adapters with
  validation before import and on-demand relative-only loading.
- **Phase 7:** added architecture enforcement for import direction, actual
  import cycles, and shared coordinator entry points across HTTP/SSE lanes.

## Recorded validation

The final recorded broad test command was:

```bash
.venv/bin/python -m pytest -q -p no:warnings src/tests/ --ignore=src/tests/unit -rf
```

At the final code revision, the result was **2,048 passed, 28 failed,
79 skipped**. The failed test identities matched the comparison baseline;
the final correction added eight passing tests. The 28 failures were
recorded as existing environment, JavaScript, and working-directory
issues. This is a historical result, not an all-green claim or a test run
against later revisions. The command excluded `src/tests/unit`.

Focused coverage included coordinator entry points, stream/pipeline
oracles, stale completion guards, persistence, cancellation, steering,
follow-up queues, agent selection, and project-adapter imports. Backend
slices retained explicitly reported browser/manual verification gaps.

## Remaining work

- Development reproducibility and startup preflight tooling remain deferred.
- Canceled-row persistence divergence remains deferred
  (`ERR-20261001-001` in the project backlog).
- Baseline test failures and browser/manual gaps need separate follow-up;
  architecture completion does not imply those checks passed.

## Documentation policy

Public documentation retains the architecture outcome, validation limits,
and links to maintained guides. Machine paths, provider-session metadata,
and the detailed agent execution diary belong in the install-local
personal overlay. Existing Git history is retained.
