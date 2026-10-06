---
name: cuttle-roadmap
description: >-
  Cuttle product roadmap in docs/ROADMAP.md — current truth, guardrails, and
  what not to build. The long 2026-08 OpenClaw/pipeline dump is install-local
  (.cuttle_global/personal/docs/roadmap-2026.md). Use when the user discusses roadmap,
  future features, Cuttle vs OpenClaw, architecture direction, or prioritizing
  initiatives.
---

# Cuttle roadmap reference

## Canonical files

| Audience | Path |
|---|---|
| **Public / agents on a fresh clone** | `docs/ROADMAP.md` |
| **This install only** (gitignored) | `.cuttle_global/personal/docs/roadmap-2026.md` — 2026-08 architecture dump |
| Socket / DeepSeek thesis | `docs/guides/MODULARITY.md` |

For **what to build next**, prefer `docs/ROADMAP.md`, GitHub issues, and (when present) `.cuttle/personal/learnings/FEATURE_REQUESTS.md`. Do not treat the 2026-08 dump as HEAD.

## Related docs (different jobs)

| Need | Where |
|---|---|
| Logged **feature requests** (`[FEAT-YYYYMMDD-XXX]`) | GitHub issues; install-local `.cuttle/personal/learnings/FEATURE_REQUESTS.md` |
| **Harness-of-harnesses / sockets / DeepSeek comparison** | `docs/guides/MODULARITY.md` |
| Agent router work items | `.cuttle/docs/agent-router-todo.md` |
| Learnings / errors backlog | install-local `.cuttle/personal/learnings/LEARNINGS.md`, `ERRORS.md` |
| Day-to-day repo conventions | `AGENTS.md` |

`.cuttle/personal/learnings/` is a **manual**, gitignored backlog (not Brain-injected, not auto-promoted).

## Agent behavior

1. **Roadmap, phases, strategy** → `docs/ROADMAP.md`. Optional local dump: `.cuttle_global/personal/docs/roadmap-2026.md`.
2. **New discrete feature idea** → GitHub issue, or `.cuttle/personal/learnings/FEATURE_REQUESTS.md` on the maintainer install.
3. Do not assume learnings and the public roadmap stay in sync.
