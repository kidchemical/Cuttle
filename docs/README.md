# Cuttle documentation (human)

Product docs for people reading the repo. **Agent how-to lives in [`.cuttle_global/docs/`](../.cuttle_global/docs/)** — do not add a third docs tree.

Canonical briefs: [`AGENTS.md`](../AGENTS.md), [`ROADMAP.md`](ROADMAP.md), [`.cuttle_global/docs/`](../.cuttle_global/docs/).

## Guides

- [Modularity / harness of harnesses](guides/MODULARITY.md) — product thesis
- [Agent router](guides/AGENT_ROUTER.md) — use-case table, `/router` commands
- [Cuttle Workers](guides/CUTTLE_WORKERS.md) — mesh design (global runbook: [`.cuttle_global/docs/cuttle-workers.md`](../.cuttle_global/docs/cuttle-workers.md))
- [Supervised coordinator](guides/SUPERVISED_COORDINATOR.md)
- [Pairing and allowlist](guides/PAIRING_AND_ALLOWLIST.md)
- [Remote access](guides/REMOTE_ACCESS.md)
- [Flask `web_chat_api.py` map](guides/WEB_CHAT_API.md) — composition root (not a refactor license)
- [Repository architecture map](architecture/repository-map.md) — whole-repo boot/deps (2026-09 audit)
- [Extension boundaries](architecture/extension-boundaries.md) — harness adapters vs surfaces vs agent-ops

Auth (bcrypt, LAN) is in [`ROADMAP.md`](ROADMAP.md). Desktop version lives in `electron/package.json`. Agent runbooks stay under `.cuttle_global/docs/`.

## Reviews

- [Security hardening 2026-09](reviews/security-hardening-2026-09.md)
- [Repository audit](reviews/repository-audit.md) · [inventory](reviews/repository-inventory.md) · [coverage ledger](reviews/coverage-ledger.md) · [graph/Discord consumers](reviews/graph-discord-consumers.md) · [cleanup plan](reviews/cleanup-plan.md) · [Discord cleanup 2026-09](reviews/discord-cleanup-2026-09.md)

## Start

From the repo root: `./start_cuttle.sh` (Linux/macOS) or the daemon on Windows — see the [README](../README.md).
