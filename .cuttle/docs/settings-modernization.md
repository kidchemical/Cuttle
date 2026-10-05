# Settings modernization

Assessment plus the first structural pass: the nine stacked groups are now
seven category tabs, each group badged with the storage world it writes.
Findings below are what drove that pass; the rest (Security, Devices & Mesh,
settings-API consolidation) is still unbuilt.

## Shipped: category tabs

Owner is `src/web/js/settings_page_tabs.js` (`CuttleSettingsTabs`) — tab
visibility, `?tab=` deep link, localStorage memory, per-tab lazy loading. It
owns no setting's storage; each panel's loader stays in
`src/web/settings_page.html` and registers itself. Same pattern as
`jobs_page.html`. Page CSS is `src/web/css/settings_page.css`.

| Tab | Groups (merged from the old list) |
|---|---|
| General | 1 General + 8 Notifications |
| Appearance | 2 Appearance (theme, animations, wallpaper) |
| Agents | 4 AI Configuration, plus the agent-CLI catalog and drop-in roots |
| Providers | Cheap completions + 3 API Keys |
| Chat voice | 5 Chat voice (TTS) |
| Devices | 6 Phone / LAN access + 7 Desktop app |
| Data | 9 Data Management |

Two things fell out of the split:

- **Scope badges.** Each group now carries `.settings-scope`
  (`data-scope=device|server|mixed`) with a tooltip, answering the first
  finding below.
- **Lazy panels.** Panels stay in the DOM and load on first activation, so
  opening Settings no longer fires API keys, LAN status, Electron status, TTS
  prefs, and the agent catalog at once. Wallpaper sync stays eager — it
  paints the page itself, not just the Appearance panel.

Deep link: `/settings_page.html?tab=agents`. Tests:
`src/tests/test_settings_page_tabs.py` (markup contract + node behavior).

## Shipped: Agents and Providers tabs (catalog-driven)

**Two dead controls removed.** "Cheap completion model" wrote
`preferred_llm_model`, which only `settings_routes` echoed back and
`query_tracker` logged — `api.llm_complete` used a hardcoded default plus an
env var no UI wrote. "Local model for cheap completions" wrote
`preferred_tools_ollama_model`, read only by `/api/llm-request`, a pipeline
endpoint no frontend calls. Worse, `BotConfig.set_preferred_llm_model` had a
stale allowlist that rejected every current model, so the save 400'd silently.

Replaced by `src/api/completion_providers.py` — a declarative registry
(`id`, label, credential env names, model env override, default model,
suggested models) plus two settings keys (`completion_provider`,
`completion_models`). `api.llm_complete` now takes its provider order and
models from it. Resolution order, asserted in
`src/tests/test_completion_providers.py`: explicit argument → env override →
Settings → provider default. Models are free-form, so a provider's newest
model works the day it ships.

**Agents tab renders `GET /api/agents`.** One card per manifest: label, `/slash`,
Ready / CLI not installed / Needs `ENV`, drop-in source badge, and the install
hint *only* when there is something to do. Nothing is hardcoded — adding an
agent is a folder drop (see `src/api/agent_harness/ADDING_AN_AGENT.md`).

This needed two new declarative manifest fields, because the credential story
was buried in `install_hint` prose:

| Field | Meaning |
|---|---|
| `credential_env` | Env vars whose presence Cuttle checks, most-preferred first. `[]` means the CLI owns its auth. |
| `auth_command` | The CLI's own login (`claude auth login`), shown as "Not signed in? Run …". |

`public_catalog()` derives `credential_present` and `ready = available and
(credential_present or no credential_env)` so Settings and the wizard cannot
disagree about "installed". Tests:
`src/tests/test_agent_catalog_auth.py`,
`src/tests/test_settings_agent_providers_ui.py`.

## Shipped: wizard reads the same catalog

`/api/wizard/status` no longer reports the retired `default_pipeline` step
(hardcoded `True` since the graphs were removed) and no longer points CLI-only
users at `api_keys`. Its steps are now `agent_cli`,
`completion_provider`, `channels`, with `next_step: agent_cli` first — without
an agent there is nothing to title — and `next_action` in plain language. The
wizard page lists every agent with its state and hint, deep-linking to
`/settings_page.html?tab=agents`.

## Wallpaper playlist rows

Each playlist row shows a thumbnail, the video title, and the channel name.
The page cannot ask YouTube directly — oEmbed sends no CORS headers — so
`GET /api/settings/video-metadata?url=…` (`api.settings_routes` →
`api.video_metadata`) proxies it server-side, caches per video id for 24h, and
derives the thumbnail URL from the id so the common case costs one fetch. The
id parser mirrors `src/web/js/youtube_id.js`; a test asserts the two agree, or
a row would show one video's title above another video's URL. Non-YouTube URLs
report `supported: false` and the row falls back to the filename plus a
placeholder glyph. Row CSS is page-owned in `css/settings_page.css`
(`.video-row-*`, `.setting-item--stack`). Tests:
`src/tests/test_video_metadata.py`, `src/tests/test_settings_video_rows.py`.

## Original sections (before tabs)

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
  surprised, and a server-side change looks like it didn't save. **Addressed:**
  every group is badged (This device / This server / mixed).
- **AI Configuration is stale vs the modular architecture.** The router
  (`agent_router`), per-agent pins (`/opencode model …`), starred models, and
  inference-mode toggle all live outside Settings; section 4's single "default
  model" select overlaps them without linking to them. **Partly addressed:**
  the group is now *Agents & Models* and links to the router editor. Router
  mode/brain, starred defaults, and per-agent pins still need their home.
- **API Keys belongs under Security**, together with OAuth provider status
  (new: `GET /api/auth/oauth/status`), auth/session controls, and the
  credential-presence indicators shipped in this batch. The OAuth setup
  guidance added to `auth.js` should be mirrored here (currently only in the
  login dialog).
- **Devices & Mesh has no home.** `ssh_host` / `ssh_identity` (`src/settings.json`),
  phone portal, and desktop app are split across sections 6–7 and an untracked
  JSON file. Proposed: one Devices & Mesh group (phone/LAN, desktop, mesh
  workers, identity path with portable default).
- **Proposed end-state grouping** (beyond what shipped):
  Integrations (API keys read-only presence, Discord, Gitea) · Security
  (OAuth status, sessions, secrets) · Devices & Mesh (mesh workers, identity
  path) — the tabs above collapse/extend toward this.
- **API-key widgets** should consume masked hints (never full secrets).
  Keep test/save behavior in the Settings component.

## Still out of scope

Visual redesign, runtime/model settings API consolidation, and account/device
roaming policy remain separate work. The storage split below preserves existing
install-wide UI behavior; it does not make server UI state account-specific.

## Shipped: scoped storage and retired-settings cleanup

`managers.settings_storage` owns persistence behind SettingsManager. Server
preferences remain in `src/settings.json`; worker/LAN configuration lives in
`src/data/config/machine_settings.json`; rail/workspace state lives in
`src/data/config/ui_state.json`. Browser preferences remain in localStorage.
Router config stays with server preferences, rather than adding a fourth file.

Existing installs split only on a guarded cold daemon start (or the offline
`core.runtime_data --apply` procedure). Unknown active keys survive; conflicting
destinations refuse migration. A real integer `schema_version: 1` replaces the
unused `version: "1.0.0"`. The health API reads the actual release version.

Retired pipeline selection/history/autostart, node-editor user preferences,
graph sandbox and pipeline limits are removed alongside their dead manager methods,
sandbox routes, doctor tombstones and chat warning banner. Channel settings now
cover Web Chat only; optional Discord REST agent-ops use their own configuration.
Cursor's live `/sandbox` option is independent and remains active.

Updates lock a stable sidecar, re-read current disk state, and atomically replace
only the owning file. Reads create no files. Corrupt/unsupported JSON cannot be
silently replaced with defaults. Settings read-modify-write routes use the locked
update interface; callers replacing an entire key intentionally own its value.

The legacy action HMAC key moves to `.cuttle/personal/secrets/` without rotation;
a shared worker token, when present in legacy JSON, is extracted there as well.
API credentials remain in `src/.env`. Auth and worker databases still hold
sensitive session/enrollment material; see `src/data/README.md`.

Tests: `test_settings_storage.py`, settings/routes, runtime migration, auth,
worker, GitHub App and architecture-boundary suites.
