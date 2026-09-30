# Cuttle Architecture Stabilization

## Objective

Before adding more major features or UI enhancements, restructure Cuttle so its active product architecture is easier to understand, test, modify, and navigate — for both humans and coding agents.

This is not a rewrite.

The goal is to:

- Preserve the current working product.
- Remove remaining obsolete architecture only when proven unnecessary.
- Give major product domains clear owners.
- Reduce implicit shared state and reverse dependencies.
- Make feature-specific code small enough that an agent can understand implementation + state + tests without reading tens of thousands of unrelated lines.
- Create stable boundaries for future features.

Treat **agent navigability** as an explicit architectural requirement.

Do not optimize for line count alone. Optimize for clear ownership, narrow interfaces, low coupling, and testable state transitions.

---

# Global Rules

These apply to every phase.

1. Execute only the current phase.
2. Stop and report before starting the next phase.
3. Establish a clean Git baseline before each phase.
4. Do not mix unrelated cleanup into a phase.
5. Preserve active behavior unless the phase explicitly retires something.
6. Add or preserve regression tests before moving boundaries.
7. Do not create circular imports.
8. New modules must not import a former monolith merely to access its globals.
9. Prefer dependency inversion over relocating code.
10. Distinguish:
   - domain state,
   - rendering,
   - persistence,
   - transport,
   - orchestration.
11. Avoid replacing one monolith with several files that all mutate the same global state.
12. Run a broader regression suite at the end of every major phase.
13. Commit each completed phase independently.

Before implementation, verify current file sizes, import relationships, and working-tree state rather than relying on historical audit numbers.

---

# Phase 0 — Architecture Baseline

## Goal

Establish a current, trustworthy map before moving more code.

## Tasks

Update the architecture inventory for:

- `web_chat_api.py`
- `app_shell.js`
- `chat_page.js`
- agent harness/kernel
- settings
- projects
- Git
- action forms
- workers
- auth/session state
- daemon/restart behavior

For each of the three monoliths, identify:

### `web_chat_api.py`

- route families,
- shared globals,
- mutable runtime state,
- reverse-importing modules,
- Flask initialization,
- chat execution,
- session/live-status handling,
- projects,
- Git,
- action forms,
- miscellaneous integrations.

### `app_shell.js`

Classify every major function and top-level binding by responsibility, especially:

- Spaces,
- Space groups,
- tab ordering,
- drag/drop,
- pane tree,
- persistence,
- activity indicators,
- workspace loading,
- shell navigation,
- layout restoration.

### `chat_page.js`

Classify responsibilities such as:

- messages,
- streaming,
- composer,
- slash commands,
- project context,
- action forms,
- attachments,
- follow-up queue,
- activity/unread state,
- agent/model controls,
- history,
- persistence,
- Git/pending changes.

Record cross-domain dependencies.

## Deliverables

Create or update:

- architecture dependency map
- monolith responsibility inventory
- shared-state inventory
- reverse-import inventory
- baseline tests
- baseline file/function/binding counts

## Stop condition

No application restructuring yet.

Report findings and stop.

---

# Phase 1 — Spaces / Shell Decomposition

## Why first

Spaces are actively under development and currently producing repeated regressions.

`app_shell.js` has accumulated enough shared state that localized changes are difficult to reason about.

This phase should directly improve the work currently causing problems.

## Goal

Give Spaces and Space Groups explicit ownership without rewriting the rest of the shell.

## Desired conceptual boundaries

Exact filenames may differ, but responsibilities should resemble:

```text
shell/
  spaces/
    state
    groups
    ordering
    drag_drop
    persistence
    activity
    rendering

  panes/
    tree
    rendering

  shell orchestration
```

Do not split merely by moving functions into arbitrary files.

## Critical design requirement

Drag/drop should have one canonical interpretation of pointer state.

Avoid separate competing logic for:

- preview position,
- reorder position,
- group membership,
- drop commit,
- boundary slop,
- group swallow/extend behavior.

Prefer something conceptually like:

```text
pointer + layout + current state
        ↓
computeDropTarget()
        ↓
canonical target
        ↓
preview
indicator
final mutation
```

The preview and committed result should consume the same computed intent.

## Tasks

1. Freeze current intended Spaces behavior with tests.
2. Inventory all Spaces-related globals/functions.
3. Define a small state model.
4. Extract pure calculations first.
5. Extract persistence.
6. Extract group logic.
7. Extract drag/drop logic.
8. Extract activity aggregation.
9. Extract rendering/DOM behavior last.
10. Leave `app_shell.js` responsible mainly for orchestration.

## Metrics

Track before/after:

- Spaces-related top-level shared bindings.
- Cross-domain mutations.
- Number of functions requiring direct shell-global access.
- Test coverage for grouping/reordering/drop behavior.

Do not use total file size as the primary success metric.

## Acceptance

An agent fixing Space group drag/drop should not need to understand unrelated shell functionality.

Run manual tests for:

- reorder within group,
- move between groups,
- move out of group,
- create/remove groups,
- reload persistence,
- multiple spaces,
- inactive-space activity,
- queued/running/unread indicators.

## Stop

Report and commit this phase before proceeding.

---

# Phase 2 — Low-Risk Backend Extraction

## Goal

Continue turning `web_chat_api.py` into an application composition root rather than a domain owner.

Do not touch the chat-turn coordinator yet.

## Candidate domains

Investigate and extract independently:

1. project HTTP responsibilities,
2. Git HTTP/service responsibilities,
3. action-form HTTP transport,
4. remaining clearly-owned settings or administrative domains.

Existing extracted Blueprints should remain independent.

`device_workers` stays as-is unless concrete coupling requires changes.

## Rules

A successful extraction does NOT mean:

> route moved from file A to file B.

A successful extraction means:

- the domain has an owner,
- business logic is not Flask-specific when unnecessary,
- tests target the domain directly,
- the new module does not reach back into `web_chat_api.py`,
- shared state is passed through narrow interfaces.

## Project/Git acceptance

Preserve:

- `/project`
- project chip/context
- project persistence
- working-directory enforcement
- Pending Changes
- Git status/diff/pull/push
- project access controls

These recently regressed and must be explicitly pinned.

## Action-form acceptance

Preserve:

- double ownership validation,
- HMAC provenance,
- restart recovery,
- persisted cards,
- Confirm/Cancel behavior.

## Stop

Complete one domain extraction at a time.

Prefer separate commits for Projects/Git and Action Forms.

---

# Phase 3 — Chat UI Decomposition

## Goal

Break `chat_page.js` into product-oriented components without changing the current chat experience.

Do not attempt to rewrite 20k+ lines in one pass.

## Suggested extraction order

Start with domains that already have reasonably clear boundaries:

1. Project context + `/project`
2. Pending Changes / Git UI
3. Slash command registry and dispatch
4. Action forms
5. Activity/unread/follow-up state
6. Attachments
7. Composer
8. Agent/model selection
9. Message/history rendering
10. Streaming lifecycle

Exact boundaries should follow actual dependency analysis.

## Important rule

Do not create:

```text
chat_part_1.js
chat_part_2.js
chat_utils.js
```

Create modules with domain ownership.

## State design

Where practical, move toward explicit state objects/stores rather than dozens of unrelated top-level mutable bindings.

Pure state transformations should be testable without a browser.

DOM code should consume state instead of implicitly becoming the state.

## Acceptance

A task such as:

> Fix `/project` autocomplete

should require reading:

- project-context implementation,
- slash-command interface,
- relevant tests,

not most of `chat_page.js`.

Likewise:

> Fix action-form Confirm after restart

should not require understanding message composer internals.

## Stop

Extract only a small set of domains per pass.

Commit and verify before continuing.

---

# Phase 4 — Shared Chat Lifecycle Backend

## Goal

Prepare the backend for eventual extraction of the chat-turn coordinator.

This is not yet the coordinator extraction.

## Identify and stabilize interfaces for

- session access,
- live status,
- delivery,
- cancellation,
- steering,
- follow-ups,
- assistant message persistence,
- execution status,
- restart/recovery state.

Reduce reverse imports into `web_chat_api.py`.

Where modules currently import monolith internals, replace those relationships with owned interfaces/services.

## Critical requirement

Do not accidentally move restart-sensitive mutable state into transient objects with different lifetime semantics.

Document state ownership explicitly:

- process lifetime,
- persistent SQLite,
- per-session,
- per-request,
- daemon-owned.

## Acceptance

Reverse imports into `web_chat_api.py` should materially decrease.

Modules should depend on purpose-specific services rather than the Flask entry module.

## Stop

Run restart/session/steer/cancel/follow-up regression suites.

Commit before coordinator extraction.

---

# Phase 5 — Chat-Turn Coordinator Extraction

## Highest-risk phase

Only begin after Phases 2 and 4 have established clear boundaries.

## Goal

Move the core chat-turn workflow out of the Flask route implementation.

The HTTP handler should increasingly resemble:

```text
authenticate
validate request
resolve session/project
call turn coordinator
serialize response
```

The coordinator should own the application-level workflow.

## Coordinator responsibilities

Potentially:

- project/context resolution,
- agent selection,
- execution request,
- native-session resume,
- cancellation lifecycle,
- progress events,
- result persistence,
- delivery,
- post-turn state.

Avoid making the coordinator responsible for:

- Flask request objects,
- HTML rendering,
- Discord/Telegram/etc.,
- vendor CLI internals,
- direct UI behavior.

Harness adapters remain responsible for vendor specifics.

## Acceptance

Web, Electron, Android, and future optional surfaces should conceptually be able to submit the same normalized Cuttle turn without reimplementing execution.

Run the broadest relevant regression suite here.

---

# Phase 6 — Adapter and Extension Boundary Hardening

## Goal

Polish Cuttle's long-term extension architecture after the core is clean.

Maintain the three boundaries:

### Agent Harness Adapters

Wrap external agent CLIs.

### Surface Adapters

Optional interfaces such as Electron, Web, Android, Discord, Slack, Telegram.

### Agent Operations / Service Integrations

External capabilities agents can invoke.

Do not mix these categories.

## BYO-CLI policy

Cuttle should currently prefer:

- discover installed CLI,
- validate CLI availability/version,
- invoke it,
- normalize capabilities/results,
- provide installation documentation.

Cuttle should NOT currently need to:

- install vendor CLIs,
- execute remote vendor installers,
- maintain vendor update logic,
- maintain package-manager-specific installation machinery.

Audit the current installer system, including Antigravity.

Unless a current supported product feature demonstrates the need for managed installation, retire executable installer machinery and preserve installation guidance only.

A future managed/curated CLI distribution can be designed separately if demand justifies it.

## Adapter trust

Review:

- project adapter approval,
- manifest validation,
- import isolation,
- `sys.path` behavior,
- module shadowing,
- capability declarations.

Do not invent an elaborate permissions framework without concrete requirements.

---

# Phase 7 — Final Architecture Enforcement

## Goal

Prevent the monoliths from quietly growing back.

## Add architectural guidance

Document explicit ownership rules.

Examples:

- New settings routes do not go into `web_chat_api.py`.
- New Spaces features go through the Spaces subsystem.
- New slash commands use the slash-command registry.
- Vendor-specific execution belongs in harness adapters.
- Surface-specific execution paths may not bypass the turn coordinator.
- External service operations do not live as arbitrary branches in project actions forever.

## Consider lightweight architecture tests

Potential checks:

- forbidden reverse imports into `web_chat_api`,
- route registration conventions,
- module-size warnings,
- manifest schema validation,
- unused/dead route detection,
- circular dependency detection.

Avoid arbitrary hard limits that encourage gaming metrics.

## Final success criteria

Cuttle should reach a state where:

- `web_chat_api.py` primarily composes the server.
- `app_shell.js` primarily composes shell components.
- `chat_page.js` primarily composes chat components.
- Feature state has explicit ownership.
- Cross-component communication uses narrow interfaces.
- Agent adapters remain independently understandable.
- Major feature areas fit comfortably into an agent's working context.
- Adding a feature should normally require touching one owned subsystem, not several monoliths.

---

# Development Freeze Policy

Until this stabilization initiative is substantially complete:

Allowed:

- Bug fixes.
- Regression repairs.
- Tests.
- Architecture work.
- Small UX fixes required to validate refactors.

Avoid:

- New major integrations.
- New experimental subsystems.
- Large feature additions.
- New alternate execution paths.
- New compatibility layers for retired experiments.

If a useful feature idea appears, record it in the backlog rather than implementing it immediately.

---

# Execution Protocol

For every phase:

1. Inspect.
2. Produce a dependency/state map.
3. State the exact intended boundary.
4. Add regression coverage.
5. Implement only that phase.
6. Run focused tests.
7. Run appropriate neighboring/broad tests.
8. Manually verify critical workflows where applicable.
9. Update architecture docs.
10. Report:
   - files changed,
   - responsibilities moved,
   - dependencies removed,
   - tests,
   - remaining risks,
   - Git status.
11. Stop.

Do not automatically continue to the next phase.

The purpose of this initiative is not simply to make the repository look cleaner.

The purpose is to make Cuttle easier to reason about, safer to change, and dramatically easier for coding agents to navigate without repeatedly introducing regressions.
