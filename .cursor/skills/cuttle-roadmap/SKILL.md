---
name: cuttle-roadmap
description: >-
  Cuttle product roadmap in docs/ROADMAP.md — current truth, guardrails, and
  what not to build. The long 2026-08 OpenClaw/pipeline dump is install-local
  (.cuttle/personal/docs/roadmap-2026.md). Use when the user discusses roadmap,
  future features, Cuttle vs OpenClaw, architecture direction, or prioritizing
  initiatives.
---

# Cuttle roadmap reference

## Canonical files

| Audience | Path |
|---|---|
| **Public / agents on a fresh clone** | `docs/ROADMAP.md` |
| **This install only** (gitignored) | `.cuttle/personal/docs/roadmap-2026.md` — 2026-08 architecture dump |
| Socket / DeepSeek thesis | `src/docs/guides/MODULARITY.md` |

For **what to build next**, prefer `docs/ROADMAP.md` plus `.cuttle/learnings/FEATURE_REQUESTS.md`. Do not treat the 2026-08 dump as HEAD.

## Related docs (different jobs)

| Need | Where |
|---|---|
| Logged **feature requests** (`[FEAT-YYYYMMDD-XXX]`) | `.cuttle/learnings/FEATURE_REQUESTS.md` |
| **Harness-of-harnesses / sockets / DeepSeek comparison** | `src/docs/guides/MODULARITY.md` |
| Agent router work items | `TODO_AGENT_ROUTER.md` |
| Learnings / errors backlog | `.cuttle/learnings/LEARNINGS.md`, `.cuttle/learnings/ERRORS.md` |
| Day-to-day repo conventions | `AGENTS.md` |

`.cuttle/learnings/` is a **manual** backlog (not Brain-injected, not auto-promoted).

## Agent behavior

1. **Roadmap, phases, strategy** → `docs/ROADMAP.md`. Optional local dump: `.cuttle/personal/docs/roadmap-2026.md`.
2. **New discrete feature idea** → `.cuttle/learnings/FEATURE_REQUESTS.md`.
3. Do not assume learnings and the public roadmap stay in sync.
