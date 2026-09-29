# Cuttle (this repo) — project rules

Applies only when the chat targets the Cuttle repo itself. Global etiquette
(always-on for every project) lives in `.cuttle_global/rules/` — do not duplicate it here.

These files under `.cuttle/rules/` are compiled into every Cuttle-chat agent turn by the
**Context Compiler** (Cuttle Brain). Keep them short — how-to lives in `.cuttle/docs/`.

## Project layout

| Path | Role |
|---|---|
| `src/api/` | Flask API + agent harness (`web_chat_api.py` port 8080, Brain, workers) |
| `src/managers/` | Settings, projects, scaffold |
| `src/web/` | Vanilla JS chat UI served by Flask |
| `electron/` | Host/Client shell (`electron/package.json` owns mesh `cuttle_version`) |
| `.cuttle_global/` | Shared global config (rules/docs/actions/scripts for every project) |
| `.cuttle/` | This repo's own commands/rules/actions/docs (this file's tree) |
| `.cuttle/learnings/` | Manual FEAT/ERR/LRN backlog (not Brain-injected) |

## Hard rules

1. **Versioning** — SemVer bump at release time only, never per push; git hash
   owns staleness/update checks → `04-versioning.md`, how-to
   `.cuttle/docs/cuttle-release.md`.
2. **Mesh-visible runtime changes need a version bump first** — Workers advertise
   `electron/package.json` `version` as `cuttle_version`. Bump via
   `.cuttle/scripts/bump-cuttle-version.ps1` → commit → emit the `git.push`
   action form (do not `git push` from the agent shell) → `workers.self-update`
   for Clients. Details → `.cuttle/docs/cuttle-release.md`.
3. **Reload ladder (least disruptive first)** — (1) hard-refresh the Cuttle shell
   when only HTML/JS/CSS changed; (2) Host Electron UI restart
   (`electron.host-restart` / `workers.self-update` `target=<host>` `no_daemon`)
   when the Host badge/`CUTTLE_PACKAGE_VERSION` must match the bump; (3) Flask
   only when Python that Flask imports changed — **emit the `flask.restart`
   action form** (prefer graceful) and let the user choose; do **not**
   autonomously `/restart when-idle` or force while they may be mid-chat.
4. **Dashboards** — usage → global `.cuttle_global/docs/dashboards.md`; adding a
   dashboard (catalog/service/routes/JS) → `.cuttle/docs/dashboards-dev.md`.
5. **Agent router backlog** — `.cuttle/docs/agent-router-todo.md` (product TODO, not a runbook).
