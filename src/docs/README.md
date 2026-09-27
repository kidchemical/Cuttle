# Cuttle documentation

Cuttle is a self-hosted control plane for guest agent CLIs (Cursor, Codex, Claude, Muse, Hermes, …): daemon + Flask (port **8080**) + web/Electron chat + optional Discord.

Canonical briefs: [`AGENTS.md`](../../AGENTS.md), [`docs/ROADMAP.md`](../../docs/ROADMAP.md), [`.cuttle/docs/`](../../.cuttle/docs/).

## Guides

- [Agent router](guides/AGENT_ROUTER.md)
- [Modularity / harness of harnesses](guides/MODULARITY.md)
- [Cuttle Workers](guides/CUTTLE_WORKERS.md)
- [Wizard and Doctor](guides/WIZARD_AND_DOCTOR.md)
- [Pairing and allowlist](guides/PAIRING_AND_ALLOWLIST.md)
- [Sandbox](guides/SANDBOX.md)
- [Remote access](guides/REMOTE_ACCESS.md)
- [Sessions API](guides/SESSIONS_API.md)
- [Skills](guides/SKILLS_REGISTRY.md)
- [Supervised coordinator](guides/SUPERVISED_COORDINATOR.md)
- [Versioning](guides/VERSIONING.md)
- [OAuth setup](guides/OAUTH_SETUP.md)
- [Authentication](guides/AUTHENTICATION_GUIDE.md)

Hub runbooks (actions, Discord, git, workers): [`.cuttle/docs/`](../../.cuttle/docs/).

## Setup

- [Setup instructions](setup/SETUP_INSTRUCTIONS.md)
- [WSL & Cursor Agent](setup/WSL_CURSOR_AGENT_SETUP.md)

## Start

From the repo root: `./start_cuttle.sh` (Linux/macOS) or the daemon on Windows — see the [README](../../README.md).
