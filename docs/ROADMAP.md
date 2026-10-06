# Cuttle roadmap

**Status:** public, self-hosted / home-lab. Not a turnkey SaaS product.

Cuttle is a **harness of harnesses**: a daemon + chat UI that hosts vendor agent CLIs (Cursor, Codex, Claude, Muse, Hermes, …), routes work, and fans jobs across a LAN mesh. The long 2026-08 architecture dump (OpenClaw comparison, old LOC counts, pipeline-era phases) lives on the install as `.cuttle_global/personal/docs/roadmap-2026.md` and is **not** the public plan.

Day-to-day backlog: [GitHub issues](https://github.com/kidchemical/Cuttle/issues). Socket thesis: [`guides/MODULARITY.md`](guides/MODULARITY.md). Flask composition root: [`guides/WEB_CHAT_API.md`](guides/WEB_CHAT_API.md) (map only — not a cue to extract).

## True today

- **Chat control plane** — web, Electron Host/Client (Discord DMs retired; optional Discord REST agent-ops)
- **Agent catalog** — `/cursor`, `/codex`, `/claude`, `/muse`, `/hermes`, `/deepseek`, `/opencode`, …
- **Router** — picks a tentacle on clean sessions; starred/sticky agents bypass it
- **Workers** — LAN mesh (file copy, shell recipes, Blender shards, self-update). Intent scheduling (`workers.plan`) is partial
- **Jobs** — mesh/worker cockpit (devices and mesh jobs). Chat is slash agents + router
- **Auth** — bcrypt passwords, CORS origin allowlist, Flask-Limiter on login/register/pairing/OAuth, `Secure` cookies on HTTPS
- **Owner** — `OWNER_USER_EMAIL`, or the first account on the host if unset; registration closes after it (`auth.allow_registration`). Remaining `is_owner: True` values are daemon/operator internals (sessions-send), not anonymous guests

## Near term

- Finish Workers host-intent / steal-friendly jobs (see design doc)
- Keep agent ops on `python -m api.*` — no product `cuttle` CLI
- Public packaging: secrets hygiene, first-run OOBE, this docs set
- Extracting `web_chat_api.py` is **backlog**, not a gate on product work — read the dependency map first

## Not on the table

- Docker-as-the-sandbox story, a public skills marketplace, 20-chat-platform parity, a Node/TypeScript rewrite, or competing with Cursor/Codex **inside** Cuttle

## Security notes (public)

Do not expose Cuttle past your LAN without reviewing CORS, pairing, and `OWNER_USER_EMAIL`. Cuttle Brain (Context Compiler) is the knowledge path for CLI harnesses.
