# Settings modernization — assessment (no redesign)

Assessment only. No visual changes were made as part of the fresh-install
functional fixes; this note records obsolete sections and a proposed grouping
for a future pass.

## Current sections (`src/web/settings_page.html`)

| # | Section | Controls |
|---|---|---|
| 1 | General | Agent name (localStorage only) |
| 2 | Appearance | Theme, UI animations (localStorage only) |
| 3 | API Keys | OpenAI / Anthropic / Discord credential inputs (server `src/.env`) |
| 4 | AI Configuration | Default cloud model select, tool Ollama model select |
| 5 | Chat voice (TTS) | TTS prefs |
| 6 | Phone / LAN access | LAN settings |
| 7 | Desktop app | Electron download/self-update |
| 8 | Notifications | Notification + chirp toggles |
| 9 | Data Management | Export/clear data |

## Findings

- **Two storage worlds, one page.** Sections 1–2 (and parts of 4–5, 8) read/write
  `localStorage` per browser, while 3, 6–7, 9 touch the server. Nothing on the
  page says which is which — a LAN phone user changing "their" theme is
  surprised, and a server-side change looks like it didn't save. A future pass
  should badge each section (This device / This server).
- **AI Configuration is stale vs the modular architecture.** The router
  (`agent_router`), per-agent pins (`/opencode model …`), starred models, and
  inference-mode toggle all live outside Settings; section 4's single "default
  model" select overlaps them without linking to them. Proposed home:
  Agents & Models (router mode + brain, starred defaults, per-agent pins,
  local Ollama models).
- **API Keys belongs under Security**, together with OAuth provider status
  (new: `GET /api/auth/oauth/status`), auth/session controls, and the
  credential-presence indicators shipped in this batch. The OAuth setup
  guidance added to `auth.js` should be mirrored here (currently only in the
  login dialog).
- **Devices & Mesh has no home.** `ssh_host` / `ssh_identity` (`src/settings.json`),
  phone portal, and desktop app are split across sections 6–7 and an untracked
  JSON file. Proposed: one Devices & Mesh group (phone/LAN, desktop, mesh
  workers, identity path with portable default).
- **Proposed grouping** (information architecture only):
  Appearance · Agents & Models · Integrations (API keys read-only presence,
  Discord, Gitea) · Security (OAuth status, sessions, secrets) ·
  Devices & Mesh · Notifications & Voice · Data.
- **Do not merge landing_page.html's copy.** The landing page carries its own
  API-key test/save widget; any redesign should converge the two on one
  component that consumes masked hints (never full secrets).

## Out of scope for this batch

Complete visual redesign, settings API consolidation, and per-device vs
per-server settings split. Those need product decisions (which settings roam,
which stay local) before code.
