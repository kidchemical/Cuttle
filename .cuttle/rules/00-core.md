# Cuttle (this repo) — project rules

Applies only when the chat targets the Cuttle repo itself. Global etiquette lives
in `.cuttle_global/rules/`; do not duplicate it here. Repo layout and owners:
`docs/architecture/repository-map.md`.

## Hard rules

0. **Coding intent router** — on Cuttle bug investigation, feature/fix
   implementation, or refactoring (including read-only investigations), read
   `AGENTS.md` first, then `docs/architecture/ARCHITECTURE_PRINCIPLES.md` and
   `docs/architecture/repository-map.md` for the owning slice before editing.
   Read `docs/architecture/development-instance-safety.md` before risky changes to
   the hosting runtime, execution/process ownership, restart behavior, auth/state,
   OR starting another instance. General conversation gets pointers, not full
   procedures; relevant architecture questions may read the docs they need.
1. **Versioning** — SemVer bump at release time only, never per push. The git hash
   owns staleness/update checks, so a push without a bump still flags Clients
   behind. Bump and release flow → `.cuttle/docs/cuttle-release.md`.
2. **Reload ladder (least disruptive first)** — (1) hard-refresh the Cuttle shell
   for HTML/JS/CSS; (2) Host Electron UI restart (`electron.host-restart`) when
   the Host badge must match a bump; (3) Flask only when Python it imports
   changed: **emit the `flask.restart` action form** and let the user choose. Do
   **not** restart it yourself while they may be mid-chat.
3. **Dashboards** — usage → `dashboards.md`; adding one → `.cuttle/docs/dashboards-dev.md`.
4. **Runtime state never lives in this checkout** — a database, settings file,
   upload, log, cache, `.env`, secret or install-local overlay resolves through
   `core.runtime_paths` (`cuttle_home()` helpers), never `__file__` or a
   repo-relative path; tests redirect it with `CUTTLE_HOME`. Not for tracked
   `.cuttle/` config or `temp/` scratch. Owner: `docs/architecture/cuttle-home.md`.
5. **New Cuttle feature / experimental UI** → `.cuttle/docs/experimental-features.md`
   (Cuttle development only, not features in guest projects).
6. **Composition roots stay thin** — never add domain behavior or private state
   to `chat_page.js`, `app_shell.js` or `web_chat_api.py`; put it in the owning
   `chat_*.js` / `shell_*.js` / `api.*` module and keep only a call site. Line
   ceilings in `src/tests/quality/` fail the build on regrowth. Owner map:
   `docs/architecture/repository-map.md`.
7. **Reuse the canonical building block** — before adding or changing a UI control
   (dropdown, modal, picker, chip, toast), a list a picker shows, or a helper,
   look it up in `docs/architecture/canonical-components.md` and use or extend
   that owner. A new primitive is built shared and registered there in the same
   change. Not for one-off page layout or copy.
