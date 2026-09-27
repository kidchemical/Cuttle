# Cuttle roadmap

**Status:** public, self-hosted / home-lab. Not a turnkey SaaS product.

Cuttle is a **harness of harnesses**: a daemon + chat UI that hosts vendor agent CLIs (Cursor, Codex, Claude, Muse, Hermes, …), routes work, and fans jobs across a LAN mesh. The long 2026-08 architecture dump (OpenClaw comparison, old LOC counts, pipeline-era phases) lives on the install as `.cuttle/personal/docs/roadmap-2026.md` and is **not** the public plan.

Day-to-day backlog: [`.cuttle/learnings/FEATURE_REQUESTS.md`](../.cuttle/learnings/FEATURE_REQUESTS.md). Socket thesis: [`src/docs/guides/MODULARITY.md`](../src/docs/guides/MODULARITY.md).

## True today

- **Chat control plane** — web, Electron Host/Client, Discord DMs
- **Agent catalog** — `/cursor`, `/codex`, `/claude`, `/muse`, `/hermes`, `/deepseek`, `/opencode`, …
- **Router** — picks a tentacle on clean sessions; starred/sticky agents bypass it
- **Workers** — LAN mesh (file copy, shell recipes, Blender shards, self-update). Intent scheduling (`workers.plan`) is partial
- **Pipelines / Node Editor** — **removed**. Chat is slash agents + router. Jobs UI is mesh/workers only
- **Auth** — bcrypt passwords, CORS origin allowlist, Flask-Limiter on login/register/pairing/OAuth, `Secure` cookies on HTTPS
- **Owner** — `/api/chat` uses `OWNER_USER_EMAIL` (or any local account if unset). Remaining `is_owner: True` values are daemon/operator internals (sessions-send), not anonymous guests

## Near term

- Finish Workers host-intent / steal-friendly jobs (see design doc)
- Keep agent ops on `python -m api.*` — no product `cuttle` CLI
- Public packaging: secrets hygiene, first-run OOBE, this docs set

## Not on the table

- Docker-as-the-sandbox story, a public skills marketplace, 20-chat-platform parity, a Node/TypeScript rewrite, or competing with Cursor/Codex **inside** Cuttle
- Treating the visual pipeline editor as the product. Graphs were removed; if a debugger comes back it is not the chat path

## Security notes (public)

Do not expose Cuttle past your LAN without reviewing CORS, pairing, and `OWNER_USER_EMAIL`. Cuttle Brain (Context Compiler) is the knowledge path for CLI harnesses. Pipeline-era per-identity `MEMORY.md` was removed.
