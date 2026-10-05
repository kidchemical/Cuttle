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
- [Headless Linux server Host](guides/HEADLESS_SERVER.md) — systemd user service, no desktop
- [Flask `web_chat_api.py` map](guides/WEB_CHAT_API.md) — composition root (not a refactor license)
- [Repository architecture map](architecture/repository-map.md) — whole-repo boot/deps (2026-09 audit)
- [Extension boundaries](architecture/extension-boundaries.md) — harness adapters vs surfaces vs agent-ops

Auth (bcrypt, LAN) is in [`ROADMAP.md`](ROADMAP.md). Desktop version lives in `electron/package.json`. Agent runbooks stay under `.cuttle_global/docs/`.

## Start

From the repo root: `./start_cuttle.sh` (Linux/macOS) or the daemon on Windows — see the [README](../README.md).

For a development checkout, install both runtime and development requirement files
before pytest; `pytest.ini` requires `pytest-timeout`. See the
[root development instructions](../README.md#development) for POSIX and Windows commands.
