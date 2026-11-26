# Cuttle documentation (human)

Product docs for people reading the repo. **Agent how-to lives in [`.cuttle_global/docs/`](../.cuttle_global/docs/)** (shared, every project) and project-specific docs in [`.cuttle/docs/`](../.cuttle/docs/). Do not add another tracked docs tree beyond `docs/`, `.cuttle_global/docs/`, and `.cuttle/docs/`. Runtime install-local overlays (`<home>/personal/`, `{project}/.cuttle/personal/`) are gitignored and are not tracked trees.

Canonical briefs: [`AGENTS.md`](../AGENTS.md), [`ROADMAP.md`](ROADMAP.md), [`.cuttle_global/docs/`](../.cuttle_global/docs/).

## Guides

- [Modularity / harness of harnesses](guides/MODULARITY.md) — product thesis
- [Agent router](guides/AGENT_ROUTER.md) — use-case table, `/router` commands
- [Agent steering](guides/AGENT_STEERING.md) — mid-turn follow-ups and background detach
- [Chat attachments](guides/CHAT_ATTACHMENTS.md) — images, PDFs and voice clips
- [Cuttle Workers](guides/CUTTLE_WORKERS.md) — mesh design (global runbook: [`.cuttle_global/docs/cuttle-workers.md`](../.cuttle_global/docs/cuttle-workers.md))
- [Execution nodes](guides/EXECUTION_NODES.md) — a chat's agent CLI on another paired machine
- [Supervised coordinator](guides/SUPERVISED_COORDINATOR.md)
- [Pairing and allowlist](guides/PAIRING_AND_ALLOWLIST.md)
- [Remote access](guides/REMOTE_ACCESS.md)
- [Headless Linux server Host](guides/HEADLESS_SERVER.md) — systemd user service, no desktop
- [Sessions API](guides/SESSIONS_API.md) — one chat sending into another
- [Script distribution audit](guides/SCRIPT_DISTRIBUTION.md) — what each tracked script is for (2026-10 inventory)
- [Flask `web_chat_api.py` map](guides/WEB_CHAT_API.md) — composition root (not a refactor license)

## Architecture

- [Architecture principles](architecture/ARCHITECTURE_PRINCIPLES.md) — ownership rules; read before non-trivial changes
- [Repository architecture map](architecture/repository-map.md) — whole-repo boot, owners and dependencies
- [Task owners](architecture/task-owners.md) — which module owns which kind of change
- [Canonical components](architecture/canonical-components.md) — the one dropdown, modal, picker and model list
- [Chat inline surfaces](architecture/chat-inline-surfaces.md) — transcript row families
- [Extension boundaries](architecture/extension-boundaries.md) — harness adapters vs surfaces vs agent-ops
- [Cuttle home](architecture/cuttle-home.md) — where runtime state lives; personal overlays
- [Development instance safety](architecture/development-instance-safety.md) — before risky runtime changes
- [CI failure patterns](architecture/ci-failure-patterns.md) — read before a release push
- Records: [monolith baseline](architecture/monolith-baseline.md), [monolith refactor plan](architecture/monolith-refactor-plan.md), [Claude native delegation RCA](architecture/claude-native-delegation-rca-2026-11-10.md), [router research notes](ROUTER_RESEARCH_NOTES.md)

Auth (bcrypt, LAN) is in [`ROADMAP.md`](ROADMAP.md). Desktop version lives in `electron/package.json`. Agent runbooks stay under `.cuttle_global/docs/`.

## Start

From the repo root: `./start_cuttle.sh` (Linux/macOS) or the daemon on Windows — see the [README](../README.md).

For a development checkout, install both runtime and development requirement files
before pytest; `pytest.ini` requires `pytest-timeout`. See the
[root development instructions](../README.md#development) for POSIX and Windows commands.
