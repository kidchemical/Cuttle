# Canonical components (reuse registry)

One implementation per capability. Before you add a control, picker, list,
helper or data fetch, find its row here and **use or extend that owner**. A
second variant that "looks close enough" is a bug: it drifts (different look,
different data, missing new options) and the user sees two products.

`ARCHITECTURE_PRINCIPLES.md` §1 states the rule ("if logic already has an
owner, call that owner"); `task-owners.md` maps *features* to owners. This file
maps *building blocks* (UI primitives, data sources, helpers) to owners, so the
owner is findable before code is written.

## Procedure (every change that adds UI or a helper)

1. **Look it up.** Find the capability below. Not listed → search for an
   existing implementation (`rg`, `python -m api.knowledge_graph query …`)
   before assuming none exists.
2. **Reuse as-is** when it fits: call the owner's entry interface.
3. **Extend the owner** when it almost fits: add an option, a host capability
   or a variant *inside the owner* (with its tests). Never fork a copy into your
   page, never restyle a copy to "match".
4. **New primitive** only when nothing exists: build it shared from the start
   (`src/web/js/shared/`, `src/core/`, or the domain owner's pure module), and
   **add its row here in the same change**.
5. **Same data, same source.** A picker on a second surface must read the same
   endpoint/contract the first surface reads, not a cheaper snapshot of it.
6. Changing an owner's look or behavior changes every consumer — that is the
   point. Check the consumers listed in its row.
7. Capability listed under **No owner yet**: do not add another copy. Either
   reuse the nearest existing one or do the consolidation first.

Reviewers: a diff that adds a hand-rolled variant of a row below is rejected,
even when it works.

## UI primitives (`src/web`)

| Capability | Canonical owner | Entry interface | Do not |
|---|---|---|---|
| Dropdown / select / combobox | `js/shared/searchable_select.js` (`CuttleSearchableSelect`); app pages auto-upgrade every native `<select>` via `js/shared/page_controls.js` (`polish(root)`) | `<select>` + `CuttleSearchableSelect.enhance(el, opts)` (`searchable:false` for short lists); opt out only with `data-native-select` | hand-rolled `<div>` menus, custom listbox/popover dropdowns, per-page dropdown CSS |
| Modal overlay | `js/shared/modal.js` (`CuttleModal.create(el)`) | `open()` / `close()` / `isOpen()` | per-page overlay show/hide logic |
| Agent picker (harness · model · effort) | modal: `js/chat/chat_chip_config.js` (`CuttleChipConfig.openAgent`); app-page adapter: `js/orchestrator/agent_pin_picker.js` (`CuttleAgentPinPicker`) | `CuttleAgentPinPicker.create({ getCatalog, onChange }).open(pin)` with a **live** catalog (see Data) | a new agent/model/effort dialog, `<select>` trio, or a page-local spec builder |
| Model/effort choices for one catalog row | `js/orchestrator/orchestrator_model.js` (`CuttleOrchestrator.pinOptions`, `pinProblem`, `settingItems`) — pure | `pinOptions(row, model)` → `{models, efforts, …}`; per-model levels from `model_efforts` | copying the row → options mapping; using the `efforts` union for a chosen model |
| Chat composer model/effort pins for a badge-gated harness (palette rows, picks, seed, badge, request pins) | `js/chat/chat_agent_controls.js` generic controller (`HARNESS_PIN_SPECS` → `agentControls.harnessPins(id)`); pure decisions in `js/chat/chat_agent_model.js` (`harness*` helpers); backend contract `/api/<id>/models\|model\|effort` | add one `HARNESS_PIN_SPECS` row + an agent-slice `routes.py` blueprint | another hand-copied `load/build/persist/seed<Agent>…` family (Muse/Hermes/OpenCode/Codex/Claude predate it) |
| Project picker / project chip | `js/chat/chat_chip_config.js` (`openProject`) | `CuttleChipConfig.create(host).openProject()` | page-local project lists |
| Avatar picker (agent profiles) | `js/orchestrator/avatar_picker.js` (`CuttleAvatarPicker.create`) | `create(opts)` | a second emoji/avatar grid |
| Toast | `js/shared/toast.js` | `showToast(msg, 'success'\|'error'\|'info'\|'warning')` | inline status banners for transient results |
| Markdown rendering | `js/chat/chat_markdown.js` (`CuttleChatMarkdown`) — pure, expects escaped text | `renderMarkdownBlocks` | a second Markdown renderer |
| Page header + frame | `css/compact_page.css` (`.compact-page-title`, `.cuttle-page`) | markup classes | header variants (guarded by `test_page_header_standard.py`) |
| Inline transcript row | family in `chat-inline-surfaces.md` | `addSystemMessage` / `CuttleChatBackgroundWork` / action form | hand-rolled transcript rows |

## Data sources

| Data | Canonical source | Notes |
|---|---|---|
| Agent catalog + **live** model/effort lists for pickers | `GET /api/agents?live_models=1` → `agent_harness.agent_models.enrich_agent_catalog`; same loaders as the chat chip's `/api/<agent>/models` | Plain `GET /api/agents` is the **manifest snapshot** — for labels, icons, availability and capability booleans only. Never feed a model or effort picker from it: new CLI models are missing. Fall back to plain only on error (`agent_profiles_page.js`). |
| One agent's model list with last-good fallback | `agent_models.load_agent_catalog` / `list_agent_models` (`model_catalog_delta` keeps the persisted last-good list) | one fallback chain; per-agent loaders return the manifest when the CLI is down |
| Server-side pin validation | `agent_models.check_agent_pin` | used by orchestrator, profiles, chat-ops, sub-agent spawn |
| Starred model/effort defaults | `agent_harness.agent_defaults` (`get_starred_model` / `get_starred_effort`) | |
| Project list | `GET /api/projects` | |

## Backend helpers (Python)

| Capability | Canonical owner | Do not |
|---|---|---|
| Runtime state paths | `core.runtime_paths` (`cuttle_home()`, `desktop_state_dir()`, `user_state_dir()`, `secrets_dir()`) | `LOCALAPPDATA` / `Path.home()` joins, `__file__`-relative state |
| Settings read/write | `managers.settings_manager` + `managers.settings_storage` | private JSON settings files |
| Vendor CLI environment | `core.agent_cli_env.agent_cli_env` | hand-built child env |
| Vendor CLI executable lookup | `agent_harness.win_cli.which_preferring_native` (prefers native `.exe` over npm `.cmd` shims on Windows) | bare `shutil.which` for a CLI that will be launched |
| Vendor CLI output drain / timeout / cancel | `scripts.utilities.agent_process.run_interruptible` (+ `attach_to_chat_run`) | a private `Popen` + wait loop |
| Atomic file write | `managers.settings_storage.atomic_write` (unique temp, fsync, Windows retry) — candidate to move to `core/` | fixed `*.tmp` names, no-fsync copies |

## No owner yet (consolidate before adding more)

Each has several divergent copies today; see the audit doc for file:line.

- Confirm / discard dialog (`window.confirm` in ~8 pages vs the in-page typed-name dialog in Projects).
- Tabs / segmented control (Settings, Tasks, Orchestrator, Spaces, chat chip segments differ in ARIA and keyboard).
- HTML escape (`esc`/`escapeHtml`, ~14 copies with different quote handling).
- Relative time / duration / byte formatting (~10 time, 2 byte copies).
- Clipboard copy with fallback (chat-local `copyTextToClipboard`).
- Agent-id alias normalization (four normalizers; `agent_defaults` has no aliases).
- Effort vocabulary (five sets; supervised normalizer silently maps unknown → `low`).
- SQLite connection helper, route error envelope, timestamp helpers (backend, low).

## Known duplication backlog

Verified audit 2026-11-18: `.cuttle/docs/dry-audit-2026-11-18.md`. Fixes that
touch Studio surfaces (`.cuttle/docs/studio-display-plan.md`) wait for that
overhaul. Remove a line when its consolidation lands.

- Orchestrator playbook, Spaces overrides and the supervision dialog read plain
  `/api/agents` for model pickers → live models (e.g. new Codex releases) missing.
- Composer model/effort pins are hand-copied per agent for Muse, Hermes,
  OpenCode, Codex and Claude in `chat_agent_controls.js` / `chat_page.js`;
  migrate each to `HARNESS_PIN_SPECS` (Antigravity already uses it).
- Catalog-row → choices is copied in `chat_supervision_model.js` and
  `shell_spaces.js`; the Spaces copy ignores `model_efforts`.
