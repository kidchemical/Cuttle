# `.cuttle/` — Cuttle repo project config

This tree holds config for the **Cuttle repo itself** (loads only for Cuttle chats).
Shared global config for **every** project lives in `.cuttle_global/`:

```text
.cuttle_global/
  rules|docs|actions|scripts|agents|keys/   # shared config (every project)
  personal/                                 # install-local global overlay (gitignored)

.cuttle/
  commands/       # Cuttle slash commands (/cleanup-sessions, /generate-3d, …)
  rules/          # Cuttle-only always-on rules (Context Compiler, Cuttle chats only)
  actions/        # Cuttle-only allowlisted side effects
  docs/           # Cuttle-only docs (release, router TODO, dashboards-dev)
  scripts/        # Cuttle-only shell helpers
  agents/         # Cuttle project drop-in harness agents (instance drop-ins: .cuttle_global/agents/)
  learnings/      # manual FEAT/ERR/LRN backlog (not Brain-injected; not scaffolded)
  memory/         # reserved
  certs/          # install TLS identity (gitignored)
  personal/       # install-local project overlay (gitignored; supplements this tree)
```

Pattern reference for guest projects: `.cuttle_global/README.md` + `.cuttle_global/docs/commands-and-actions.md`.
New projects are scaffolded by `managers.cuttle_scaffold.ensure_cuttle_scaffold`
(guest trees mirror this project shape — they do **not** get a `.cuttle_global/` copy).
