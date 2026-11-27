# Cuttle roadmap

**Status:** public, self-hosted / home-lab. Not a turnkey SaaS product.

Cuttle is a **harness of harnesses**: a daemon + chat UI that hosts vendor agent CLIs (Cursor, Codex, Claude, Muse, Hermes, …), routes work, and fans jobs across a LAN mesh. An older architecture dump may exist in an install-local overlay; this document is the public plan.

Day-to-day backlog: [GitHub issues](https://github.com/kidchemical/Cuttle/issues). Socket thesis: [`guides/MODULARITY.md`](guides/MODULARITY.md). Flask composition root: [`guides/WEB_CHAT_API.md`](guides/WEB_CHAT_API.md) (map only — not a cue to extract).

## True today

- **Chat control plane** — web, Electron Host/Client (Discord DMs retired; optional Discord REST agent-ops)
- **Agent catalog** — `/cursor`, `/codex`, `/claude`, `/muse`, `/hermes`, `/deepseek`, `/opencode`, …
- **Router** — picks a tentacle on clean sessions; starred/sticky agents bypass it
- **Workers** — LAN mesh (file copy, shell recipes, Blender shards, self-update). Intent scheduling (`workers.plan`) is partial
- **Jobs** — mesh/worker cockpit (devices and mesh jobs). Chat is slash agents + router
- **Auth** — bcrypt passwords, CORS origin allowlist, Flask-Limiter on login/register/pairing/OAuth, `Secure` cookies on HTTPS
- **Owner** — `OWNER_USER_EMAIL` selects an email or username; if unset, the oldest active non-guest account is the owner. Registration closes once a non-guest account exists and can be reopened with `auth.allow_registration`. Remaining `is_owner: True` values are daemon/operator internals (sessions-send), not anonymous guests

## Near term

- Develop Workers host-intent scheduling and steal-friendly jobs (see the [Workers guide](guides/CUTTLE_WORKERS.md))
- Keep agent ops on `python -m api.*` — no product `cuttle` CLI
- Public packaging: secrets hygiene, first-run OOBE, this docs set
- Extracting `web_chat_api.py` is **backlog**, not a gate on product work — read the dependency map first

## Technical debt

- **Context-size budget test is fragile.** `test_fresh_envelope_stays_inside_its_budget` asserts a fixed character
  count (guest 11,500) on a briefing that embeds the checkout path, so the same tree passes in a short worktree
  path and fails in a longer clone, and independent rule edits collide: `655201d2` alone passes with 32 characters
  to spare, but adding a pending 3-line `00-core.md` rule plus capability text gives 11,775. Measure the briefing
  without path-dependent text (or budget per section) and decide who trims when two changes together overflow.

## Not on the table

- Docker-as-the-sandbox story, a public skills marketplace, 20-chat-platform parity, a Node/TypeScript rewrite, or competing with Cursor/Codex **inside** Cuttle

## Security notes (public)

Do not expose Cuttle past your LAN without reviewing CORS, pairing, and `OWNER_USER_EMAIL`. Cuttle Brain (Context Compiler) is the knowledge path for CLI harnesses.
