# Cuttle (this repo) — project rules

Applies only when the chat targets the Cuttle repo itself. Global etiquette
(always-on for every project) lives in `.cuttle_global/rules/` — do not duplicate it here.

These files under `.cuttle/rules/` are compiled into the fresh/full Cuttle-chat context by the
**Context Compiler** (Cuttle Brain) and ride change-deltas on resume. Keep them short — how-to lives in `.cuttle/docs/`.

## Project layout

| Path | Role |
|---|---|
| `src/api/` | Flask API + agent harness (`web_chat_api.py` port 8080, Brain, workers) |
| `src/managers/` | Settings, projects, scaffold |
| `src/web/` | Vanilla JS chat UI served by Flask |
| `electron/` | Host/Client shell (`electron/package.json` owns mesh `cuttle_version`) |
| `.cuttle_global/` | Shared global config (rules/docs/actions/scripts for every project) |
| `.cuttle/` | This repo's own commands/rules/actions/docs (this file's tree) |
| `.cuttle/personal/learnings/` | Install-local FEAT/ERR/LRN backlog (gitignored; not Brain-injected) |

## Hard rules

0. **Coding intent router** — on Cuttle bug investigation, feature/fix
   implementation, or refactoring (including read-only investigations), read
   `AGENTS.md` first, then `docs/architecture/ARCHITECTURE_PRINCIPLES.md` and
   `docs/architecture/repository-map.md` for the owning slice before editing.
   Read `docs/architecture/development-instance-safety.md` before risky changes to
   the hosting runtime, execution/process ownership, restart behavior, auth/state,
   OR starting another instance. General conversation gets pointers, not full
   procedures; relevant architecture questions may read the docs they need.
1. **Versioning** — SemVer bump at release time only, never per push; git hash
   owns staleness/update checks → `04-versioning.md`, how-to
   `.cuttle/docs/cuttle-release.md`.
2. **Mesh updates ride git revs, not version bumps** — `needs_update` is SemVer
   mismatch OR git-rev mismatch OR stale boot rev (`platform.py`); a push without
   a bump still flags Clients behind. Bump `electron/package.json` SemVer at
   release time only (`04-versioning.md`), then commit → emit the `git.push`
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

6. **Runtime state never lives in this checkout** — when code creates or
   writes a database, settings/config file, upload, log, cache, `.env`, secret
   or install-local overlay, resolve it through `core.runtime_paths`
   (`cuttle_home()` helpers), never `__file__`/`src/`/repo-relative paths; tests
   redirect it with `CUTTLE_HOME`. Not for tracked `.cuttle/` config or
   `temp/` scratch. Owner: `docs/architecture/cuttle-home.md`.

7. **New Cuttle feature / experimental UI** — follow `.cuttle/docs/experimental-features.md`
   for default rollout practice and surface placement. The Experimental tab owns
   toggles, not feature workflows. This guidance applies to Cuttle development,
   not features in guest projects.
