# Cuttle documentation (human)

Product docs for people reading the repo. **Agent how-to lives in [`.cuttle/docs/`](../.cuttle/docs/)** — do not add a third docs tree.

Canonical briefs: [`AGENTS.md`](../AGENTS.md), [`ROADMAP.md`](ROADMAP.md), [`.cuttle/docs/`](../.cuttle/docs/).

## Guides

- [Modularity / harness of harnesses](guides/MODULARITY.md) — product thesis
- [Agent router](guides/AGENT_ROUTER.md) — use-case table, `/router` commands
- [Cuttle Workers](guides/CUTTLE_WORKERS.md) — mesh design (hub runbook: [`.cuttle/docs/cuttle-workers.md`](../.cuttle/docs/cuttle-workers.md))
- [Supervised coordinator](guides/SUPERVISED_COORDINATOR.md)
- [Pairing and allowlist](guides/PAIRING_AND_ALLOWLIST.md)
- [Remote access](guides/REMOTE_ACCESS.md)
- [Sessions API](guides/SESSIONS_API.md)

Auth (bcrypt, LAN) is in [`ROADMAP.md`](ROADMAP.md). Desktop version lives in `electron/package.json`. Agent runbooks stay under `.cuttle/docs/`.

## Start

From the repo root: `./start_cuttle.sh` (Linux/macOS) or the daemon on Windows — see the [README](../README.md).
