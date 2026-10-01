# Cuttle Architecture Stabilization — Phase Review Log

Canonical handoff artifact for external review. One self-contained section per
phase. Do not rely on chat history; everything a reviewer needs is below.

Related (not duplicated here): `docs/architecture/repository-map.md`,
`docs/guides/WEB_CHAT_API.md`, `docs/guides/AGENT_ROUTER.md`,
`docs/reviews/architecture-audit-checkpoint-2026-09.md` (2026-09-28 point
investigation: retired-pipeline closure, auth-split verification).

Conventions: all line numbers, counts, and import lists below were measured
against the stated baseline commit with the commands quoted in each phase.
Historical audit numbers are never reused without re-verification.

---

# Phase 0 — Architecture Baseline

## Phase status

- Phase number and name: Phase 0 — Architecture Baseline.
- Git baseline before work: `a7e035d2` ("Add functionality to open files in
  default applications from the UI"), branch `main`.
- `git status --short` at start: clean (empty). At end: clean except this
  new untracked review file (no application code touched).
- Git commit after work: none (Phase 0 is read-only by design; stop condition
  is "no application restructuring yet").
- Completion status: **complete**.

## Original problem

Before moving code, establish a current, trustworthy map of the active product
architecture: which files own which responsibilities, where shared mutable
state lives, which modules import "up" into the Flask monolith, and what the
test baseline actually is. Prior audits (notably the 2026-09-28 checkpoint)
contain point measurements that may already be stale; every number below was
re-measured on `a7e035d2`.

## Architecture before

This *is* the "before" picture. No restructuring was performed.

### Monolith sizes (verified: `wc -l`, `du -b`)

| File | Lines | Bytes | Structure |
|---|---|---|---|
| `src/api/web_chat_api.py` | 12,732 | 511,130 | 302 top-level fns, 334 incl. nested; 37 module-level assigns |
| `src/web/js/chat_page.js` | 26,700 | ~1.21 MB | 940 named `function` decls, whole file wrapped in one IIFE (`(function() { 'use strict'; ... })()` at line 2); zero top-level `const/let/var` |
| `src/web/js/app_shell.js` | 7,943 | ~320 KB | 314 named `function` decls, 99 top-level bindings, script scope (not IIFE-wrapped) |

Largest single function in the Flask monolith: `chat_endpoint`
(`web_chat_api.py:5495`, ~1,171 lines through ~6665) — chat ingress, sticky
agent dispatch, attachment pre-pass, persistence, and streaming in one body.

### web_chat_api.py — route families (verified: 184 `.route(` occurrences, 170 unique paths, 137 unique `/api/*` paths)

- **Page/static serving (~30):** `/`, `app_shell.html`, `chat_page.html`,
  `landing_page.html`, `settings_page.html`, `git_ui.html`,
  `git_graph_page.html`, `jobs_page.html`, `job_insight.html`,
  `dashboards_page.html`, `router_editor.html`, `node_editor.html`,
  `control_panel.html`, `task_management.html`, `wizard_page.html`,
  `about_page.html`, `media_player.html`, `apps_page.html`,
  `home_automation.html`, `tools_page.html`, `query_log.html`,
  `test_reports.html`, `pipeline_chat.html`, `phone`, `/js|css|img|sounds|logs|output|git-webui` static.
- **Chat core (~12):** `/api/chat`, `/api/chat-cancel`, `/api/chat-steer`,
  `/api/chat-pending-result`, `/api/chat-live-status`,
  `/api/chat-live-status-batch`, `/api/chat/warnings`, `/api/query-live-status`,
  `/api/query-log/<id>`, `/api/turn-feedback`, `/api/llm-request`,
  `/api/prompt/enhance`.
- **Harness model/effort (~20):** `/api/{codex,muse,hermes,opencode}/{models,model,effort}`,
  `/api/cursor-agent/models`, `/api/agent-defaults/<agent_id>`,
  `/api/agents`, `/api/agent-context`, `/api/agent-context/compact`,
  `/api/agent-router/options`.
- **Router (~8):** `/api/router/{config,health,health/refresh,demotion/clear}`,
  `/api/router/config` GET+PUT, outcomes/frustration/use-case helpers live
  behind these handlers as lazy imports.
- **Git (~18):** `/api/git/{status,repos,pending-changes,pending-diff,commit,ignore,suggest-commit-message,graph,commits,commit/<hash>/{detail,diff,file-diff},files,open-diff,open-file,pull,push,branch,branches}`.
- **Projects/tasks (~16):** `/api/projects[/<id>[/switch|sync]]`,
  `/api/projects/{current,history,stats}`, `/api/project-commands`,
  `/api/tasks[/<id>[/comments[/<cid>]|close-via-commit]]`.
- **Action forms (~4):** `/api/action-form/{run,dismiss,watch-state,followup-message}`.
- **Restart (~4):** `/api/flask/restart{,/status,/notify}`, legacy `/api/restart`.
- **Shell/panes (~4):** `/api/shell/{panes,panes/<n>/messages,workspaces,workspaces/<id>}`.
- **Sessions (~6):** `/api/sessions[/list|/send|/<id>|/clear-session]` —
  plus auth-blueprint session routes under `/api/auth/*`.
- **Home automation (~9):** `/api/home-automation/{providers,themes,apply-theme[+-stream],apply-schedule-now[+-stream],schedule,auto-tick,status}`.
- **Mobile (~3):** `/api/mobile/{events,poll,reply}` + `/phone` landing.
- **Media/upload (~6):** `/api/upload`, `/api/shared-media/{stage,poster,meta,purge}`.
- **Infra/ops (~15):** `/api/{health,status,doctor,debug-log,net-insight,executing-jobs,cuttle-jobs,job-insight,job-watch/cancel}`.
- **Process launchers (~6):** `/api/{start,stop}-{launcher,webapi,discord}`,
  `/api/start-ungit`, `/api/ungit/status`.
- **Keys/models (~6):** `/api/{load-api-keys,save-api-key,test-api-key,ollama-models}`,
  `/api/local-llm/{start,stop,status}`.
- **Supervised (~2 in monolith):** `/api/supervised/tasks/<id>/{control,runs/<rid>/raw}`
  (heavier supervised logic lives in `agent_router/supervised/`).
- **Misc (~6):** `/api/{toast,ui-toasts,fs/open,fs/reveal,lan-ping,network-info,wizard/status}`,
  `/api/pairing/{approve,pending,status}`, `/api/test-reports`.

Flask initialization (no `create_app` factory; single global `app`):
`app = Flask(__name__)` at line 213; CORS restricted to safe origins
(`lan_access.build_cors_origins`); `limiter.init_app(app)` at 267;
blueprints `auth_bp` (297), `workers_bp` (302), `dashboards_bp` (307),
`settings_bp` (315); function-registered routes for chat-widgets, chat-TTS,
web terminal, desktop/electron, mobile-android-update (328–357); self-signed
TLS cert generation for LAN; `_OWNER_EMAIL` import-time snapshot from env.

### web_chat_api.py — shared globals / mutable runtime state

Module-level (lines ~380–438, 731–733, 3013), all lost on Flask restart:

- `chat_sessions = {}` (380), `session_counter = 0` (381) — only 3
  `chat_sessions[` mutation sites and 4 `session_counter` references; the
  counter is incremented under `global` in `get_or_create_session` (417–419).
  Durable chat history lives in `auth_db` (SQLite), not here.
- `retired_pipeline_registry = {}` (384) — graph era ended; intentionally
  always empty, kept so health/sessions payload shapes don't break.
- `_chat_status_queues: dict` (433), `_chat_live_status: dict` (437) guarded
  by `_chat_live_status_lock` (438); TTLs 45 min / 90 s connecting.
- `_pending_local_llm_messages: dict` (731),
  `_declined_local_llm_sessions: set` (733) — local-LLM launch gate.
- `_shell_pane_layout` (3013) — reassigned under `global` in the panes API
  (9137); workspaces additionally persisted via settings manager helpers.
- Only 2 `global` statements in the whole file (419, 9137); most cross-request
  state is dict/set mutation, not rebinding.

Outbound coupling: 446 `import`/`from` statements total, of which 251 are
**lazy function-level** `from api.* / managers.*` imports spanning ~73
distinct `api.*`/`managers.*` modules (full list verified via
`grep -o "from \(api\|managers\)\.[A-Za-z0-9_.]*" | sort -u`; notable families:
`agent_harness.*`, `agent_router.*` incl. `supervised.*`, `chat_*`,
`cuttle_jobs.*`, `device_workers.routes`, `dashboards.routes`,
`settings_routes`, `mobile_*`, `subagents.*`, `query_tracker`,
`active_executions`, `job_watch`, `chat_run_registry`, `shared_media`,
`vision_prepass`, `starred_slash`, `project_*`, `bundled_llm_tools`).
Top-level imports are only stdlib/Flask plus `chat_status_phases`, `limiter`,
`http_authz` — the lazy-import pattern is load-bearing (avoids circulars with
the reverse importers below) and must be preserved by any extraction.

### Reverse-import inventory (prod code importing the monolith)

`grep -rn "web_chat_api" src/api src/managers src/scripts src/core`
(non-test, non-comment hits — each verified in context):

| Importer | Symbol used | Coupling note |
|---|---|---|
| `agent_harness/kernel.py:71` | `_default_chat_cwd` (lazy) | private helper reaches *into* Flask for CWD fallback |
| `internal_http.py:61` | `wca.app.test_client()` (lazy) | in-process POST fallback couples HTTP layer to Flask app object |
| `chat_delivery.py:221,266` | `get_chat_live_status`, `clear_chat_live_status` (lazy) | live-status read/write owned by monolith |
| `auth_api.py:621,696` | `active_live_session_ids` (lazy, ×2) | auth blueprint reads Flask live-session set |
| `subagents/turns.py:92` | `_assistant_message_metadata` (lazy) | private metadata builder reused by child-chat turns |
| `subagents/identity.py:106` | `_user_badge_metadata` (lazy) | private badge builder reused by subagent identity |
| `agent_router/supervised/orchestrator.py:225` | `set_chat_live_status` (lazy) | supervised loop writes monolith live status |
| `agent_router/supervised/adapters.py:103,123` | `_run_codex_web_command`, `_run_cursor_web_command` (lazy) | default runners call *private* Flask handlers directly |
| `agent_router/dispatch.py:48` | `_run_harness_web_command` (lazy) | default runner factory calls private Flask handler |
| `chat_status_phases.py:26` | `emit_chat_status` (lazy) | status-phase helper re-enters Flask emit |
| `chat_run_registry.py:776` | `clear_chat_live_status` (lazy) | registry cleanup touches monolith live status |

Comment-only mentions (no import): `limiter.py` (documents why the limiter
lives outside Flask), `settings_routes.py`, `agent_harness/__init__.py`,
`web_terminal.py`, `cuttle_ui_capabilities.py`,
`cuttle_managed_process_guard.py` (kill-guard patterns). Test-only importers:
~25 test modules (expected; they pin current behavior).

### app_shell.js — responsibility inventory (7,943 lines)

Script scope (not IIFE-wrapped); 314 named functions, 99 top-level bindings,
132 `addEventListener`, 24 `fetch`, 27 `/api/` references. Keyword
classification of the 314 function names (verified): spaces 75, workspace 20,
tabs/order 13, drag/drop 8, panes 21, persist-family 10, activity/unread 12,
git 6, project 3, history 2, stream 2, attach 1, followup 1.

- **Spaces + space groups (dominant, ~75 fns):** create/rename/delete,
  grouping, membership, switching, space-scoped navigation.
- **Workspace loading + layout restoration (~20 fns):** boot sequence,
  persisted layout load/save, column/row geometry, safe-page validation
  (`_SHELL_PAGE_SAFE_RE` analog on client).
- **Tab ordering + drag/drop (~21 fns):** tab list model, reorder, cross-pane
  and cross-column drag, drop targets.
- **Pane tree + split columns (~21 fns):** column model, split/close, ordinal
  refs ("1st pane"), per-pane iframe lifecycle.
- **Persistence (10 fns):** `GET/PUT /api/shell/workspaces`,
  `GET /api/shell/panes`, local prefs; server snapshot helpers mirror
  `_shell_panes_snapshot` / `_workspace_column_summary` in the monolith.
- **Activity indicators (12 fns):** per-pane unread/activity dots fed by
  `GET /api/chat-live-status-batch` polling + `/api/executing-jobs`.
- **Shell navigation:** rail, app switching, OAuth (`/api/auth/oauth/google`),
  logout/me, LAN ping, toasts, debug-log, mobile/Android hooks, worker
  self-update + SSH-approval polling.
- Backend coupling is narrow: shell owns layout, Flask owns snapshot shapes —
  the `/api/shell/*` contract is the boundary to protect in later phases.

### chat_page.js — responsibility inventory (26,700 lines)

Single IIFE, `'use strict'`; 940 named functions, 151 listeners, 67 `fetch`
calls, 111 `/api/` references. Keyword classification of the 940 names
(verified): history 141, agent/model 111, message 80, slash 80, persist 65,
activity/unread 67, project 54, git 45, composer 23, followup 26, attach 16,
action-form 14, stream 9, panes 15, tabs/order 7.

- **Messages + history (largest, ~220 fns):** bubble render, markdown,
  nav/virtualization (`messageNav`), search (`chat_find.js` companion),
  history panel + delete modal, session list, rename, persistence to
  `/api/auth/sessions/*`.
- **Agent/model controls (~111 fns):** harness badges/chips, per-agent model +
  effort pickers (`/api/{codex,muse,hermes,opencode}/{models,model,effort}`,
  `/api/cursor-agent/models`, `/api/agent-defaults/*`), context gauge
  (`/api/agent-context`), compact (`/api/agent-context/compact`).
- **Slash commands (~80 fns):** palette, supplement, sticky/starred prefix,
  `starred-slash` + `starred-project` settings endpoints.
- **Streaming (~9 fns + SSE glue):** `/api/chat` stream consume,
  `/api/chat-pending-result`, `/api/chat-steer`, `/api/chat-cancel`,
  live-status (`/api/chat-live-status`), TTS (`/api/chat/tts`).
- **Composer (23 fns):** input, send, Enhance (`/api/prompt/enhance`),
  turn feedback (`/api/turn-feedback`).
- **Project context (54 fns):** chip, picker (`/api/projects*`),
  reconcile-after-pick, per-turn snapshot attach.
- **Action forms (14 fns):** card render + `run/dismiss/watch-state/
  followup-message` calls.
- **Attachments (16 fns):** picker, `/api/upload`, `/output/shared` staging,
  thumbnail re-render from metadata.
- **Follow-up queue (26 fns):** queue/dequeue UI around server queue.
- **Activity/unread (67 fns):** badges, chirp, chrome menu, toasts.
- **Git/pending changes (45 fns):** companions `git_ui.js` (1,375 lines),
  `pending_changes_panel.js` (1,205), `git_commit_viewer.js` (798),
  `git_graph_page.js` (1,354) — real sub-domain, calls `/api/git/*`,
  `/api/fs/{open,reveal}`.
- **Cross-file coupling:** `chat_page.js` exposes a small `window.*` surface
  (`toggleChatHistoryPanel*`, `CuttleMediaThumbFallback`,
  `__lastSupervisedTask`, `_e2eChat` test hooks, `__cuttlePinChatLayout`
  consumer); `app_shell.js` ↔ `chat_page.js` communicate via iframe + pane
  messaging, not shared JS scope (good — no JS global-state merge risk).
- Companion modules already extracted and healthy: `auth.js` (1,444),
  `chat_widgets.js`, `supervised_control.js` (460), `router_editor.js` (742),
  `shared_navigation.js`, `ui_boot.js`, `toast.js`, `net_debug.js`.

### Other domains (verified sizes via `find … | xargs wc -l`)

| Domain | Files | Lines | Owner entry points / notes |
|---|---|---|---|
| Agent harness + kernel | 34 | 7,705 | `agent_harness/kernel.py` (`run_agent_web_command`), `catalog.py`, per-agent `agents/{cursor,codex,muse,claude,hermes,opencode,antigravity,deepseek}/{adapter,session_store,model_catalog}`; `steer.py`, `cwd.py`, `timeouts.py`, `types.py` |
| Agent router | 46 | 12,212 | `engine.py`, `dispatch.py`, `integration.py`, `registry.py`, `config.py`, `supervised/` (14 files: orchestrator, delivery, store, adapters, control, bubble, …), `eval/` (6 files) |
| Workers mesh | 17 | 6,679 | `device_workers/` (routes blueprint `workers_bp` already extracted from monolith) |
| Subagents | 11 | 2,757 | `subagents/service.py`, `store.py`, `turns.py`, `identity.py` — but `turns`/`identity` reach back into Flask privates (see reverse imports) |
| Settings | — | 371 (`settings_manager.py`) | `SettingsManager` (JSON file, get/set per key); HTTP surface already extracted to `settings_routes.py` (`settings_bp`) |
| Projects | — | 634 (`project_manager.py`) | project CRUD + history/stats; HTTP handlers still in monolith (`get_projects`, `create_project`, …) |
| Tasks | — | 560 (`task_manager.py`) | supervised-task-adjacent CRUD; HTTP handlers still in monolith |
| Action forms | — | 1,641 (`action_forms.py`) | domain extracted; Flask keeps 4 thin `/api/action-form/*` handlers |
| Auth/session | — | 1,310 + 2,011 + 37 (`auth_api.py`, `auth_db.py`, `auth_session.py`) | `auth_bp` blueprint extracted; `AuthDatabase` (SQLite) owns users, auth sessions, chat sessions, follow-up queue, session projects — the durable counterpart to Flask's in-memory maps |
| Git | — | handlers in monolith (~18 routes) + `commit_message_suggester.py` | no `managers/git*` owner; frontend split across 4 JS files; candidate for first extraction |
| Daemon/restart | — | 1,442 (`scripts/cuttle_daemon.py`) | spawns/supervises Flask, tray, workers; `perform_flask_restart` + `watch_flask_restart_requests` + health watch; protocol files `cuttle_flask_restart_request.json` / `cuttle_flask_restart_status.json`; Flask side in `flask_restart.py` (1,195) |
| Discord | 6 | 478 (`discord_ops` 157 + `discord_cli` 321) | optional agent-ops only, no inbound gateway |
| `src/scripts` total | 44 | 17,473 | CLIs, utilities, daemon |
| `src/managers` total | 6 | 2,962 | settings, projects, tasks, scaffold… |
| `src/core` total | 5 | 820 | shared core helpers |
| Tests | 175 files | — | pytest default config excludes e2e + hardware/secret-dependent unit tests (see `pytest.ini`) |

### Cross-domain dependency map (ASCII)

```
chat_page.js (IIFE, 940 fns) ──111 /api refs──▶ web_chat_api.py (12,732 L)
app_shell.js (314 fns) ──27 /api refs──▶ web_chat_api.py ──lazy imports──▶ ~73 modules
                                                        ◀──reverse imports── 11 prod modules
web_chat_api.py ──blueprints──▶ auth_bp │ workers_bp │ dashboards_bp │ settings_bp
web_chat_api.py ──register_*──▶ chat_widgets │ chat_tts │ web_terminal │ desktop │ mobile-update
daemon ──spawn/supervise──▶ Flask ──request file──▶ daemon ──restart──▶ Flask (generation+1)
Flask in-memory (sessions/live-status/panes/LLM-gate) ──restart──▶ LOST (see below)
auth_db (SQLite) ──durable──▶ users/sessions/history/followups/projects (survives restart)
```

### Shared-state inventory (restart-sensitive state marked *)

| State | Owner | Scope | Survives Flask restart? |
|---|---|---|---|
| `chat_sessions`, `session_counter` | monolith | process | No* (rebuild on demand; durable history in `auth_db`) |
| `_chat_live_status`, `_chat_status_queues` | monolith (+ writers in orchestrator, `chat_delivery`, `chat_status_phases`) | process + lock | No* (TTL 45m/90s; cancel-safe read paths exist) |
| `_shell_pane_layout` + workspace store | monolith + settings mgr | process + persisted | Partial (persisted snapshot survives; live layout may not) |
| `_pending_local_llm_messages`, `_declined_local_llm_sessions` | monolith | process | No* (launch gate re-asks) |
| `retired_pipeline_registry` | monolith | process | N/A (always empty by design) |
| `AuthDatabase` SQLite | `auth_db.py` | disk | Yes |
| `settings.json` via `SettingsManager` | `managers/` | disk | Yes |
| `_OWNER_EMAIL` snapshot | monolith import time | process | Only via restart w/ new env |
| Daemon restart request/status JSON | daemon + Flask | disk | Yes (that's the point) |

## Changes made

None to application code — Phase 0 stop condition ("no application
restructuring yet") was honored. Only this review file was created. No code
deleted, relocated, or otherwise moved; no compatibility layers added.

## Architecture after

Identical to "Architecture before" above — this phase produces the map, not
the move. Ownership boundaries as measured:

- Flask monolith owns: all `/api/*` handlers except the 4 extracted
  blueprints + 5 `register_*` route groups; chat orchestration; live status;
  pane snapshots; in-memory session maps.
- Extracted and healthy (do-not-touch-without-cause): `auth_bp`,
  `workers_bp`, `dashboards_bp`, `settings_bp`, chat-widgets/TTS/terminal/
  desktop/mobile-update route groups, `action_forms.py`, `flask_restart.py`,
  `limiter.py`, `http_authz.py`.
- Next-extraction candidates (large, monolith-resident, clear seams): Git
  routes (~18), projects/tasks routes, shell workspace routes, harness
  model/effort routes, home-automation routes, process launchers.
- Hard coupling to break before any move: 11 prod reverse-importers of Flask
  privates (table above), especially `adapters.py`/`dispatch.py` calling
  `_run_*_web_command` directly and `internal_http.py` reaching
  `wca.app.test_client()`.

## Dependencies and state

- Dependencies removed: none (baseline).
- Dependencies introduced: none.
- Remaining reverse dependencies: 11 prod modules (full table under
  "Reverse-import inventory" — the single biggest structural risk for Phase 1+;
  any extraction of `_run_*_web_command`, live-status helpers, or assistant/
  badge metadata builders must preserve or migrate these call sites).
- Shared mutable state remaining: see "Shared-state inventory" table; the
  Flask-process maps (`chat_sessions`, `_chat_live_status`,
  `_shell_pane_layout`, LLM-gate sets) are the state that must never be split
  across modules that each mutate the global (per Global Rules: prefer
  dependency inversion, don't relocate code just to share globals).
- Persistence/restart-sensitive state: all in-memory Flask maps are lost on
  restart (documented per-row above); durable state is `auth_db` SQLite +
  `settings.json` + restart protocol JSON.
- Compatibility layers retained: `retired_pipeline_registry` (always-empty
  dict for payload shape), legacy `/api/restart`, pipeline-chats fs shims
  (per 2026-09-28 checkpoint; re-verified present, not re-audited here).

## Tests and verification

- Exact test commands (from repo root, `.venv`):
  - `.venv/bin/python -m pytest -q` (default `pytest.ini` config: e2e and
    hardware/secret-dependent paths ignored) — run twice; same 28 failures
    both runs.
  - `.venv/bin/python -m pytest src/tests/ -x -q --co` (collection probe) —
    1,818 collected with 1 collection error in the force-included
    `src/tests/unit/test_security.py` (`ModuleNotFoundError:
    multi_stage_processor`; that file is excluded by default config, so the
    default suite is unaffected).
- Counts (default suite): **1,710 passed, 28 failed, 60 skipped** in ~62 s.
- Failing tests (all pre-existing on the clean `a7e035d2` tree; no code was
  changed, so none are regressions from this phase):
  - `test_agent_defaults.py::test_agent_defaults_post_rejects_capability_violations`
  - `test_agent_router_drift.py::test_health_api_round_trip`
  - `test_chat_attachments.py` (3: refresh-persist, image-description, JSON round-trip)
  - `test_chat_false_reply_ready.py::test_cursor_stream_switch_does_not_chirp_mid_run`
  - `test_chat_history_delete_modal.py::test_history_delete_modal_lives_in_the_pane_not_the_sidebar`
  - `test_chat_history_search.py` (2: asset-versions bump, full-list-paint gate)
  - `test_codex_cli.py::test_codex_models_endpoints`
  - `test_codex_starred_effort.py::test_codex_execution_passes_starred_effort_to_cli`
  - `test_dashboards.py::test_flask_routes` (`KeyError: 'dashboards'`)
  - `test_dashboards_usage.py::test_usage_route`
  - `test_hermes_session_pins.py::test_hermes_turn2_passes_resume_on_argv`
  - `test_muse_cli.py` (3: auto-mode dispatch, stream model forward, models endpoints)
  - `test_project_chip_persistence.py` (2)
  - `test_shell_workspaces.py::test_workspace_api_save_list_replace_delete`
  - `test_stop_refresh_live_status.py::test_live_status_endpoint_idle_when_cancelled_even_if_row_lingers`
  - `test_subagents_ui.py::test_subagent_markup_present`
  - `test_supervised_coordinator.py::test_palette_includes_followup_control`
  - `test_supervised_forensics.py` (2), `test_supervised_hardening.py` (1)
  - `test_ui_layout_apps.py` (2)
- Failure clusters (triage only, not diagnosed): harness model-endpoint
  tests (codex/muse/hermes, ~6), supervised-coordinator forensics (~4),
  frontend-asset/markup assertions (~5), project-chip/shell-workspace
  persistence (~3), dashboards blueprint wiring (~2). These are the suites
  Phase 1 must get green (or explicitly disposition) before moving the
  boundaries they cover.
- Manual workflows exercised: none (Phase 0 is inventory + suite baseline;
  no behavior changed, so no manual verification was applicable).
- Workflows NOT exercised: live Flask boot, chat round-trip, restart
  protocol, LAN/pairing, workers mesh, Discord ops — none claimed as verified.

## Metrics

| Metric | Value (on `a7e035d2`) | Method |
|---|---|---|
| `web_chat_api.py` lines / bytes | 12,732 / 511,130 | `wc -l`, `du -b` |
| `.route(` occurrences / unique paths / unique `/api/*` | 184 / 170 / 137 | `grep -c`, regex extract + `sort -u` |
| Top-level / total functions in monolith | 302 / 334 | `ast` walk |
| Module-level assigns / `global` stmts | 37 / 2 | `ast` + `grep` |
| Lazy `api./managers.` imports / distinct modules | 251 stmts / ~73 modules | `grep -o … \| sort -u` |
| Prod reverse-importers of monolith | 11 modules, ~14 symbols | `grep -rn web_chat_api` + context read |
| `chat_page.js` lines / functions / fetch / `/api/` refs | 26,700 / 940 / 67 / 111 | `wc`, regex, `grep -o` |
| `app_shell.js` lines / functions / top-level bindings | 7,943 / 314 / 99 | same |
| Harness files / lines | 34 / 7,705 | `find + wc` |
| Router files / lines | 46 / 12,212 | `find + wc` |
| Device-workers files / lines | 17 / 6,679 | `find + wc` |
| Subagents files / lines | 11 / 2,757 | `find + wc` |
| Managers files / lines | 6 / 2,962 | `find + wc` |
| Daemon lines | 1,442 | `wc -l` |
| Test files / collected | 175 / 1,818 | `ls`, `--co` |
| Suite result | 1,710 pass / 28 fail / 60 skip | `.venv/bin/python -m pytest -q` ×2 |

Note: the 2026-09-28 checkpoint cites "229 `@app.route`"; current tree
measures 184 `.route(` occurrences. The difference is method/age (that count
likely included blueprint-registered routes or predates removals), not an
error in either — current-tree numbers above are the ones Phase 1 must use.

## Remaining concerns

1. **28 pre-existing test failures** on the clean tree (clusters listed
   above). Phase 1's "regression tests before moving boundaries" rule means
   these must be fixed or formally dispositioned first — especially the
   supervised, shell-workspace, project-chip, and harness-endpoint suites
   that sit directly on candidate extraction seams.
2. **Reverse-import direction is wrong in 11 places.** The deepest: router
   `adapters.py`/`dispatch.py` depend on Flask *private* runner functions;
   `internal_http.py` depends on the Flask `app` object itself. Extraction
   order should move runners/status/metadata *down* into owned modules first,
   then re-point Flask — not the reverse.
3. **`chat_endpoint` (~1,171 lines)** mixes ingress, dispatch, pre-pass,
   persistence, streaming. It is the highest-risk future seam; needs
   characterization tests before any split.
4. **chat_page.js has no module structure** (one IIFE, 940 functions). Any JS
   split must define the `window.*` surface contract first (currently ~8
   exposures plus iframe messaging).
5. **In-memory vs durable session split** is implicit: newcomers can't tell
   `chat_sessions` (ephemeral) from `auth_db` chat sessions (durable) without
   this document. A later phase should name the distinction in code.
6. **Uncertainty:** exact behavior of several failing suites (e.g. whether
   dashboard `KeyError` reflects a registration-order bug or a stale test)
   was deliberately not diagnosed — Phase 0 records, does not fix.

## Diff summary

- Files added: `docs/reviews/architecture-stabilization.md` (this file).
- Files modified: none.
- Files deleted: none.
- Insertions/deletions: new file only (~450 lines); `git status --short`
  shows only `?? docs/reviews/architecture-stabilization.md`.
- Application code: zero changes (verified: `git diff --stat` empty).

## External Review Summary

1. **What changed architecturally?** Nothing — Phase 0 is a read-only
   baseline. Deliverable is the map: route families (184 routes, 137 `/api/*`
   paths), 37 module-level state bindings, 11 prod reverse-importers, JS
   responsibility inventories (940 + 314 functions), domain sizes, and the
   restart-sensitive state table.
2. **What behavior intentionally changed?** None.
3. **What behavior should be identical?** Everything (no code touched).
4. **What remains coupled or messy?** (a) 11 prod modules import Flask
   privates, including router defaults calling `_run_*_web_command` and HTTP
   infra reaching `app.test_client()`; (b) 1,171-line `chat_endpoint`;
   (c) structureless 26.7k-line `chat_page.js`; (d) 28 failing tests on the
   clean tree, clustered on future extraction seams; (e) implicit
   ephemeral-vs-durable session split.
5. **What should be reviewed before beginning the next phase?** Confirm the
   reverse-import table is complete; disposition the 28 failures
   (fix vs. accept); agree the first extraction seam (recommendation: Git
   routes — ~18 handlers, no reverse-importers, 4-file JS counterpart
   already coherent).
6. **Is the next phase safe to begin?** Yes, with the precondition that
   Phase 1 starts by fixing/dispositioning the failing suites that cover its
   chosen seam, per the Global Rules ("add or preserve regression tests
   before moving boundaries"). The baseline commit is `a7e035d2` with a
   clean tree; this file is the only delta and is docs-only.

---

# Phase 1 — Spaces / Shell Decomposition

## Phase status

- Phase number and name: Phase 1 — Spaces / Shell Decomposition.
- Git baseline before work: `1f789211` ("Baseline: authoritative stabilization
  plan Phases 0-7 (external review input)"), clean tree.
- Git commit after work: the single `Phase 1 Spaces/Shell decomposition`
  commit on main (one commit for the whole phase; identify via
  `git log --oneline`, not by hash, since a doc-only amend finalizes it).
- Completion status: **fully closed** — implementation complete,
  automated verification green (53/53 focused; full suite failures
  identical to baseline), manual browser smoke test passed 2026-09-30
  with no regressions. Rendering extraction remains deliberately deferred
  (see Remaining concerns); it is future work, not an open Phase 1 item.

## Original problem

`app_shell.js` (~7.9k lines, 306 top-level functions) accumulated Spaces
state, grouping, ordering, drag/drop, persistence, and activity logic as
globals-sharing functions. Space-group drag/drop in particular had three
competing interpretations of pointer state (preview insertion, preview join
highlight, commit membership) that could disagree, producing repeated
regressions. An agent fixing a drop bug had to understand the whole shell.

## Architecture before

- All Spaces logic (~83 functions across state/groups/order/drop/activity/
  menus/render) lived in `src/web/js/app_shell.js` (7,943 lines), closing
  over module-level singletons: `spacesState`, `SPACE_GROUP_COLORS`,
  `SPACE_GROUP_DEFAULT_COLOR`, `SPACE_ACTIVITY_RANK/LABEL`,
  `spaceActivityBySession`, `STORAGE_SPACES`.
- Drop commit derived order by re-reading the preview-mutated DOM
  (`reorderSpacesAroundHidden(visibleIds-from-DOM)`) and membership by a
  separate neighbor inference (`fixDraggedTabGroup(draggedId, dropX)`),
  while preview used inline midpoint/span logic plus a strict hover
  hit-test (`spaceGroupSleeveAtPoint`, no slop) — three rules, two slop
  conventions (strict vs left-4/right+6).
- Characterization tests were source-text assertions (`assert "function
  foo(" in src`, some pinning code comments), not behavior.

## Changes made

New subsystem `src/web/js/spaces/` (loaded via `<script>` before
`app_shell.js`, API surface `window.CuttleSpaces`, `module.exports` under
node for tests). Every module is DOM-free (geometry/storage injected):

- `spaces_state.js` (211 L): state shape, palette (exact 10 presets),
  ids, `sanitizeLoadedData` (frozen load rules), `load/saveSpacesState`
  with injected storage, `nextSpaceName`, `addSpaceToState`,
  `plan/commitSpaceClose`, `renameSpaceInState`.
- `spaces_groups.js` (192 L): pure transitions taking state —
  `setGroup` (incl. toggle-off + Chrome adjacency), `createGroupForSpace`,
  `removeFromGroup`, `rename/setColor`, `dissolveGroup`,
  `toggleGroupCollapsed` (refuses hides-active, no toast inside),
  `planGroupDelete`, `pruneEmptyGroups`, `moveNextToGroup`.
- `spaces_order.js` (52 L): `computeReorderedIds` (visible-order commit,
  hidden slots kept) + `applyVisibleOrder`.
- `spaces_drop.js` (266 L): canonical `computeDropTarget({spaces, groups,
  visibleIds, draggedId, lanes, tabRects, sleeveRects, x})` returning
  `{order, groupId, noop, place}` consumed by BOTH preview (`place`) and
  commit (`order` + `groupId`); `applyDropTarget` commits + prunes.
- `spaces_activity.js` (135 L): `RANK/LABEL`, owned snapshot map (+reset),
  `sidVariants`, `note/lookup/clearSessionActivity`, `followupKind`,
  `selectSpaceActivity` (priority + seen-suppression, exact port).
- `app_shell.js` keeps: the `spacesState` singleton, storage binding,
  all rendering (`renderSpaceTabs`, pill html, menus, bubbles, dots DOM),
  orchestration (`switchSpace`, `add/close/renameSpace` sequencing, poll
  transport, drag event wiring, `measureSpaceDropLanes`), plus thin
  wrappers that bind the singleton and own persist/render tails.
- Deleted from shell: `fixDraggedTabGroup`, `reorderSpacesAroundHidden`,
  `moveSpaceNextToGroup`, `spaceGroupSleeveRect/AtPoint`, palette consts,
  rank/label consts, activity map, `sanitizeSpaceColor`, `newSpaceId`,
  `newSpaceGroupId`, `nextSpaceName`, `followupKind` (~420 lines removed,
  ~240 added back as delegation).
- `app_shell.html`: 5 spaces `<script>` tags before `app_shell.js`
  (all `?v=20260930spaces1` cache-busted).
- Tests: 4 new behavioral files (26 tests, node-executed against the real
  modules); repaired 8 source-text pins in `test_space_tab_groups.py` /
  `test_space_activity_dots.py` to assert new owners + delegation (intent
  preserved, see below); added load-order test.
- Code moved vs deleted: logic relocated verbatim-or-faithfully into owned
  modules (moved); the three-way drop-rule split and two sleeve-measure
  helpers deleted as superseded by the canonical path (deleted, no
  callers left).

## Architecture after

```
src/web/js/spaces/          pure domain (node-testable, no DOM/globals)
  spaces_state.js           shape, palette, sanitize, load/save, lifecycle
  spaces_groups.js          membership transitions (state in, state out)
  spaces_order.js           visible-order commit (hidden slots kept)
  spaces_drop.js            computeDropTarget -> {order, groupId, place}
  spaces_activity.js        ranking + owned snapshot map
src/web/js/app_shell.js     orchestration + rendering (singleton, storage,
                            DOM measure, event wiring, persist/render tails)
         measure (lanes) ──▶ computeDropTarget ──┬──▶ preview (place)
                                                 └──▶ commit (order+groupId)
```

Dependency direction is one-way: shell → `CuttleSpaces.*`. Modules never
read shell globals (verified: no `spacesState`, `document`, `localStorage`,
or `window.*` access in `spaces/` — only the `typeof window` namespace
bootstrap line; storage and geometry are injected). Cross-file JS scoping
unchanged (classic scripts + namespace; no bundler introduced).

## Dependencies and state

- Dependencies removed: shell-internal couplings — preview↔commit via
  preview-mutated DOM reads; commit's neighbor inference as a second rule;
  strict hover hit-test as a third rule; 15 top-level shared bindings
  (palette, rank/label, activity map, id/color/name helpers).
- Dependencies introduced: shell → `CuttleSpaces` namespace (43 call
  sites, all same-file script scope + node `require` for tests). No new
  runtime, build, or backend dependencies. No backend routes touched.
- Remaining reverse dependencies: none new (frontend has no import graph;
  `chat_page.js` iframe messaging untouched).
- Shared mutable state remaining: the `spacesState` singleton (shell-owned,
  explicitly passed into every module call — dependency inversion, not
  global sharing); activity snapshot map (now module-owned with reset);
  poll timers/scheduling flags (shell transport). Rendering still reads the
  singleton directly (deferred, below).
- Persistence/restart-sensitive state: unchanged semantics —
  `localStorage shell_spaces_v1` via injected storage; save/load round-trip
  pinned by test.
- Compatibility layers: none added/removed.

## Tests and verification

- New characterization (behavioral, node-executed, all passing):
  `test_spaces_drop.py` (11: within-group reorder, between-members join,
  move-out left-of-sleeve, outsider-stays-out, ±6 end-slop boundary, front
  slot + pill join, member-stays, NaN conservative, collapsed-never-gains,
  `place` directives, apply commit),
  `test_spaces_state.py` (6: sanitize, empty-reject + active fallback,
  save/load round-trip, lifecycle, exact legacy palette parity, parse),
  `test_spaces_groups.py` (5: assign/adjacency/toggle-off, first-stays,
  color/rename/dissolve/delete, collapse refusal, parse),
  `test_spaces_order_activity.py` (3: hidden slots, priority + both
  seen-suppression directions, parse).
- Differential proof (scratch, `/tmp/drop_diff_probe*.js`, not committed):
  original HEAD functions vs new modules end-to-end (preview sim + commit)
  over 1,134 (drag, x) positions across single-group, two-group, and
  collapsed fixtures — **zero mismatches**. NaN path additionally confirmed
  against HEAD (`[A,C,D,B]`/ungrouped — the old comment's "members stay in"
  does not hold at end position; preserved as-is, unreachable via UI).
- Repaired pins (8 tests in `test_space_tab_groups.py` /
  `test_space_activity_dots.py`): same intents, new owners asserted
  (palette, persist, canonical target, hidden-slot order, lane measure,
  sleeve-end park, collapse, priority) + no-forked-copy guards.
- Focused: 52/52 Spaces tests pass (`test_space*`, `test_pane_space_drag`).
- Broad: `.venv/bin/python -m pytest -q` → **1,736 passed, 28 failed,
  60 skipped**; the 28 failures are byte-identical to the Phase 0 baseline
  list (verified via `diff`) — all unrelated to the Phase 1 boundary
  (harness endpoints, supervised, frontend-asset, dashboards, rail layout).
  Boundary-adjacent `test_shell_workspaces` 401 and `test_ui_layout_apps`
  rail failures dispositioned as unrelated (backend auth gate / rail, no
  Spaces functions involved) and preserved.
- `node --check` clean on all 5 new modules + `app_shell.js`.
- Manual browser smoke test: **passed** (operator-verified in the actual
  Cuttle UI after commit `667bfaeb`, reported 2026-09-30). All eight plan
  acceptance gestures exercised with no regressions observed:
  reorder within a group, moving between groups, moving out of a group,
  group create/remove behavior, persistence after reload, multiple Spaces,
  inactive-space activity indicators, drag/group highlight behavior.
  (The agent-side environment has no browser; the pure-logic equivalents
  of all eight were already covered by the new node-executed tests.)

## Metrics

| Metric | Before (`1f789211`) | After | Method |
|---|---|---|---|
| `app_shell.js` lines | 7,943 | 7,704 (−239) | `wc -l` |
| Top-level fns in shell | 306 | 269 (−37) | regex `^function` |
| Top-level shared bindings | 99 | 94 (−5 net; 15 removed, ~10 orchestration glue added) | regex `^(const\|let\|var)` |
| Spaces domain fns requiring shell-global access | ~83 (all) | 0 in modules (state passed as arg; no `spacesState`/`document`/`localStorage`/`window.*` reads — only the namespace bootstrap) | grep |
| Drop interpretations of pointer state | 3 (preview insert, hover highlight, commit neighbor) | 1 (`computeDropTarget`) | code |
| Sleeve slop conventions | 2 (strict hover vs −4/+6 commit) | 1 (−4/+6 everywhere; highlight now truthful in +6 end zone — intentional micro-fix, documented) | code |
| Behavioral drop/group/order/activity tests | 0 (string pins only) | 26 node-executed | pytest |
| Differential old-vs-new drop positions | — | 1,134/1,134 match | scratch probe |
| Full suite | 1,710 pass / 28 fail / 60 skip | 1,736 pass / 28 fail (identical list) / 60 skip | pytest ×2 runs compared |

## Remaining concerns

1. **Rendering not extracted** (`renderSpaceTabs`, pill html, tab/group
   menus + bubbles, dots DOM patching, `sync*` fns remain in shell reading
   the singleton). Deliberate: "rendering last" per plan, and moving it
   now would have ballooned blast radius. A Phase 1 follow-up (or Phase 3
   frontend work) should extract `spaces_render.js` with an injected
   shell adapter; the `window.*` surface is small.
2. **Highlight micro-change**: in the +6px past-end slop zone the sleeve
   now highlights (commit joins there) where the old strict hover did not.
   Preview is now truthful; flagging for the manual browser pass.
3. **NaN-drop comment inaccuracy** (old comment vs code, preserved as-is).
4. **Workspace snapshot routes** (`/api/shell/workspaces` 401 failure) and
   **rail layout** failures are pre-existing, unrelated, preserved.
5. **Manual browser verification now done** — operator smoke test passed
   2026-09-30 (all 8 acceptance gestures, no regressions); concern closed.

## Diff summary

- Added: `src/web/js/spaces/` (5 files, 856 L total),
  `src/tests/test_spaces_{drop,groups,order_activity,state}.py` (4 files).
- Modified: `src/web/js/app_shell.js` (543 changed: +~120/−~420),
  `src/web/app_shell.html` (+5 script tags, `?v` bump),
  `src/tests/test_space_tab_groups.py`, `src/tests/test_space_activity_dots.py`
  (pin repairs only).
- Deleted: no files; ~7 superseded shell functions (see Changes).
- Docs: this Phase 1 review section.
- `git status --short` before commit: 4 modified + 6 new paths (above).

## External Review Summary

1. **What changed architecturally?** Spaces domain logic left
   `app_shell.js` for five DOM-free owned modules under `src/web/js/
   spaces/` behind the `CuttleSpaces` interface; the shell kept the
   singleton, rendering, and orchestration. The three competing drop rules
   collapsed into one canonical `computeDropTarget` shared by preview and
   commit.
2. **What behavior intentionally changed?** One micro-fix: sleeve highlight
   in the +6px past-end slop zone now matches the joining commit (was dark
   while the drop joined). Everything else is behavior-preserving (1,134
   differential positions match).
3. **What behavior should be identical?** Tab order/group outcomes for every
   drop position, group CRUD + collapse rules, palette, persistence shape,
   activity priority + seen-suppression, poll transport, all DOM structure.
4. **What remains coupled or messy?** Spaces rendering + menus still read
   the shell singleton directly; `switchSpace`/pane-tree coupling untouched
   (correctly — panes domain); 28 unrelated baseline failures preserved.
5. **What should be reviewed before beginning the next phase?** The
   `computeDropTarget` contract (inputs/outputs in `spaces_drop.js`
   header); the highlight micro-fix; the deferred rendering extraction;
   confirmation that Phase 2 must not touch the Spaces seam.
6. **Is the next phase safe to begin?** Phase 1 is self-contained (no
   backend, no chat, no router touched; failures identical to baseline)
   and its manual-browser precondition is now met (smoke test passed
   2026-09-30). Phase 2 readiness is a review decision, not an automatic
   gate-pass. Do NOT begin Phase 2 in this track until explicitly
   authorized.

---

# Phase 2 — Slice 1: Projects HTTP Extraction

## Phase status

- Slice: Phase 2 Slice 1 — Projects HTTP transport → `api.project_routes`.
- Git baseline before work: `72ede1ec` ("Phase 1 close-out: record manual
  browser verification (passed)"), clean tree.
- Git commit after work: the single `Phase 2 slice 1: projects HTTP
  extraction` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No further
  Phase 2 slices started (Git, Action Forms untouched).

## Original problem

`web_chat_api.py` owned every `/api/projects*` handler inline (~300 lines
including the `requires_project_manager` guard): route registration,
auth gating, and response shaping for project CRUD + history/stats lived
in the monolith while the actual domain logic already had an owner
(`managers.project_manager`). Any agent touching project transport had to
work inside the 12.7k-line composition root, and the auth matrix for these
recently-regressed routes had no single obvious home.

## Slice selection

Re-checked the dependency map before changing code. Candidates ranked:

1. **Projects HTTP (chosen):** 10 handlers, uniformly thin
   (manager call + jsonify), zero lazy `api.*` imports inside handlers,
   zero prod reverse-deps on the handler functions, pre-existing owned
   service (`project_manager`), exact blueprint precedent
   (`settings_routes.py`). Smallest blast radius with a real ownership win.
2. **Git HTTP (deferred):** ~1,750 lines / 21 handlers with embedded
   subprocess logic, repo resolution, and `fs_reveal` / suggester coupling;
   no owned service module yet — needs a service layer first, too big for
   a first slice.
3. **Action-form transport (deferred):** 4 handlers but HMAC provenance +
   restart-recovery semantics inline; needs careful interface design,
   scheduled after Projects proves the pattern.

## Architecture before

- All 10 project routes registered via `@app.route` in the monolith
  (lines ~11902–12197): GET list/one/current/history/stats
  (`authenticated_required`), POST create/switch/sync, PUT update, DELETE
  (`owner_required`), each additionally guarded by the local
  `requires_project_manager` decorator (503 when the manager import
  failed).
- Test stubbing reached the manager through the monolith's module
  attribute (`wca.project_manager`).

## Changes made

- **Added `src/api/project_routes.py`** (new owner): `projects_bp`
  blueprint (`url_prefix="/api"`), the `requires_project_manager` guard
  moved in, all 10 handlers moved verbatim (same paths, methods,
  decorators, bodies, log lines, status codes, payload shapes). Module
  docstring states the ownership contract (transport only; logic in
  `project_manager`; auth by decorator only; 503 by guard only) and the
  Phase 2 rule: never imports `api.web_chat_api` (verified — only a
  docstring mention).
- **Monolith:** registers `projects_bp` (same try/except pattern as
  `settings_bp`); deleted the 10 handlers (~296 lines) and the now-unused
  guard + `functools.wraps` import. `project_manager` import stays (other
  handlers still use it: chat execution, `project_commands_list`, path
  resolution).
- Monolith size: 12,732 → 12,429 lines (−303).
- **Tests:** new `src/tests/test_project_routes.py` (4 tests, written
  pre-move against the monolith, green both sides): route-registration
  contract (10 routes × methods, no duplicates), read shapes + 404,
  401 anon / 403 guest matrix, owner validation matrix (400s), success
  shapes incl. switch/500-on-manager-false, 503 for all 10 routes when
  the manager is None. Stub helper `_set_pm` patches every holder module
  so it survives the move. Repaired `test_http_authz._stub_project_registry`
  to stub the blueprint's reference too (shared instance).
- Moved vs deleted: handlers relocated verbatim (moved); the monolith's
  guard definition deleted as superseded (deleted; single new home).

## Architecture after

```
web_chat_api.py ──registers──▶ projects_bp (api/project_routes.py)
                                   │ transport only
                                   ▼
                          managers.project_manager (logic, unchanged)
```

One-way dependency: `project_routes` → `managers.*`, `api.http_authz`,
Flask. No reach-back into the monolith. The `project_manager` singleton
is still constructed once (`managers.project_manager`); both the monolith
(remaining users) and the blueprint hold a reference to the same object.

## Dependencies and state

- Removed: 10 route registrations + guard from the composition root; the
  monolith is no longer the owner of any `/api/projects*` path.
- Introduced: `api.project_routes` module (Flask Blueprint; no new
  runtime deps). No new shared state — handlers are stateless w.r.t. the
  transport layer; all state stays in `project_manager`/SQLite as before.
- Reverse deps: none existed on the moved functions (verified); none created.
- Persistence/restart semantics: unchanged (manager-owned; no in-memory
  transport state existed).
- Compatibility: no shims; paths/methods/shapes/codes/auth identical
  (pinned by tests).

## Tests and verification

- New `test_project_routes.py`: 4/4 pass pre-move AND post-move.
- `test_http_authz.py` (auth matrix incl. project reads/writes): passes
  with the repaired stub helper — 50/50 combined with the new file.
- Broad: `.venv/bin/python -m pytest -q` → **1,740 passed, 28 failed,
  60 skipped**; the 28 failures are byte-identical to the Phase 0/1
  baseline list (verified via `diff`) — all unrelated to this slice.
- `ast.parse` clean on both touched Python files; `grep` confirms no
  `web_chat_api` import in the new module and no `requires_project_manager`
  remnants in the monolith.
- Manual workflows: none applicable (no UI changed; route contract
  covered by tests). Not exercised: live Flask boot, chat round-trip.

## Metrics

| Metric | Before (`72ede1ec`) | After | Method |
|---|---|---|---|
| `web_chat_api.py` lines | 12,732 | 12,429 (−303) | `wc -l` |
| `/api/projects*` handlers owned by monolith | 10 | 0 | grep |
| New owned modules | — | `api/project_routes.py` (10 routes) | — |
| Project route tests | auth-matrix only (in http_authz) | +4 contract/shape/guard tests | pytest |
| Full suite | 1,736 / 28 / 60 | 1,740 / 28 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. `wca.project_manager` reference remains for non-extracted users (chat
   execution, project-commands, path resolution) — expected; later slices
   narrow it further.
2. `/api/project-commands` (`project_commands_list`) deliberately left in
   the monolith: different deps (`project_commands` module + cwd
   resolver), separate slice candidate.
3. Tasks routes (`/api/tasks*`) still inline — natural companion for a
   later Projects-adjacent slice, not this one.
4. The 28 baseline failures are untouched and unrelated.

## Diff summary

- Added: `src/api/project_routes.py`, `src/tests/test_project_routes.py`.
- Modified: `src/api/web_chat_api.py` (−303 lines net: −296 handlers,
  −15 guard/import, +8 blueprint registration),
  `src/tests/test_http_authz.py` (stub helper covers both holders).
- Deleted: no files.
- `git status --short` before commit: 3 modified + 2 new paths (above).

## External Review Summary

1. **What changed architecturally?** Project HTTP transport moved from
   the Flask monolith to an owned `projects_bp` blueprint; the monolith
   now only composes it. Domain logic did not move (already owned).
2. **What behavior intentionally changed?** Nothing.
3. **What behavior should be identical?** All 10 route paths, methods,
   auth matrix (reads authenticated, writes owner, 503 without manager),
   payload shapes, and status codes (400/404/500 cases pinned).
4. **What remains coupled or messy?** Git + Action Forms + tasks still
   inline; monolith still holds a `project_manager` reference for
   remaining users; 28 unrelated baseline failures preserved.
5. **What should be reviewed before the next slice?** The ownership
   contract header in `project_routes.py`; whether Git or Action Forms
   goes next (recommendation: Action Forms transport — small, owned
   service exists — before the larger Git service-layer split).
6. **Is the next slice safe to begin?** This slice is self-contained
   (no chat/router/workers touched; failures identical to baseline). Do
   NOT begin the next slice in this track until this review is approved.

---

# Phase 2 — Slice 2: Action Forms HTTP Transport

## Phase status

- Slice: Phase 2 Slice 2 — Action Forms HTTP transport →
  `api.action_form_routes`.
- Git baseline before work: `805b774c` ("Phase 2 slice 1: projects HTTP
  extraction to owned blueprint"), clean tree.
- Git commit after work: the single `Phase 2 slice 2: action-form HTTP
  transport extraction` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. Git
  extraction explicitly NOT started.

## Original problem

The four `/api/action-form/*` handlers lived inline in the Flask monolith
(~284 lines) while the domain already had an owner (`api.action_forms`).
Transport concerns — auth gating, toast/error shaping, restart-status
attach, one-shot lock persist, resume inject — were fused into the
composition root, and the HMAC/security invariants had no single obvious
HTTP home. Any agent touching form transport worked inside the 12.4k-line
monolith.

## Architecture before

- `run` / `dismiss` / `watch-state` / `followup-message` registered via
  `@app.route` in the monolith (lines ~7400–7683), each with inline lazy
  imports (`http_authz`, `action_forms`, `project_actions`,
  `flask_restart`) plus two monolith-namespace globals (`get_auth_db`,
  `get_request_session_token`).
- Per-route contracts (frozen and re-pinned):
  - `run`: token/spec/form-id required (400 toast shape); session-bound
    chat access with toast-mapped errors, else plain authentication
    (401); double ownership inside `execute_action_form_submission`
    (`owner_user_id` + spec session binding); flask.restart progress
    attach; one-shot lock persist to history; resume inject
    (`injected_user_message`, never persisted here); result JSON passed
    through; 500 silent-toast shape.
  - `dismiss`: form_id(s) merge (cap 40); shared `flask-restart-gN`
    controller ids stripped **pre-auth by design**; 400 missing session;
    skipped shape when empty; chat-session access; per-form history lock;
    no side effects.
  - `watch-state`: 400 missing form/session; authentication only (no
    chat-ownership check — intentional, pinned); snapshot persist.
  - `followup-message`: 400 missing spec; chat-session access; project
    path resolution; assistant-text rewrite persisted to history.
- Test stubbing reached `get_auth_db` through both `api.auth_db` and the
  monolith's namespace; several fixtures patch both.

## Changes made

- **Added `src/api/action_form_routes.py`** (new owner): `action_forms_bp`
  blueprint (`url_prefix="/api"`), all 4 handlers moved verbatim (same
  paths, methods, bodies, log lines, status codes, payload shapes) except
  the two monolith-namespace globals (`get_auth_db`,
  `get_request_session_token`) converted to the file's existing
  function-lazy import pattern — same late binding the other five lazy
  imports already used, so all existing test patch points keep working.
  Module docstring states the ownership contract and the Phase 2 rule:
  never imports `api.web_chat_api` (verified by import + grep).
- **Monolith:** registers `action_forms_bp` (same try/except pattern);
  deleted the 4 handlers (~284 lines). No other monolith code touched.
- Monolith size: 12,429 → 12,152 lines (−277 net).
- **Tests:** new `src/tests/test_action_form_routes.py` (9 tests, written
  pre-move against the monolith, green both sides): route-registration
  contract; anon/guest/owner/wrong-chat matrix per route; tampered +
  garbage tokens expire silent; restart-recovered Confirm (history +
  memory flush) and Cancel; one-shot lock + `already_locked`; dismiss
  ownership + pre-auth controller skip; follow-up ownership + persist;
  watch-state auth/shapes. Repaired
  `test_form_resume_has_a_single_bubble_writer` to read the new owner
  module (intent preserved + asserts the monolith no longer defines the
  route). Retained `test_action_forms.py` + process-restart HMAC suites
  untouched.
- Moved vs deleted: handlers relocated (moved, one documented
  import-pattern normalization); no domain logic moved or redesigned;
  no obsolete code removed (dependency closure not yet verified for the
  surrounding form system).

## Architecture after

```
web_chat_api.py ──registers──▶ action_forms_bp (api/action_form_routes.py)
                                   │ transport only
                                   ▼
              api.action_forms (domain) + project_actions + flask_restart
```

One-way dependency: `action_form_routes` → `api.action_forms`,
`api.project_actions`, `api.flask_restart`, `api.http_authz`,
`api.auth_db`, `api.auth_session`, Flask. No reach-back into the
monolith; no helpers duplicated (the two converted references use the
same lazy-import pattern, not copies). Restart-sensitive state untouched
(in-memory pending map + history recovery stay in `action_forms`).

## Dependencies and state

- Removed: 4 route registrations from the composition root; the monolith
  no longer owns any `/api/action-form/*` path.
- Introduced: `api.action_form_routes` (Flask Blueprint; no new runtime
  deps). No new shared state; no state moved.
- Reverse deps: none existed on the moved functions (only a source-text
  test, repaired); none created.
- Security invariants preserved (all pinned by tests): chat-session
  access gating per route; double ownership (`owner_user_id` + spec
  session binding enforced in `execute_action_form_submission`);
  server-signed inline tokens (HMAC, `CUTTLE_ACTION_HMAC_SECRET`);
  tampered/garbage tokens expire silent; spec_override is locator-only
  (tampered-spec test retained in `test_action_forms.py`); persisted-card
  + history recovery incl. restart flush; one-shot Confirm/Cancel locks;
  dismiss + follow-up ownership; watch-state auth-only (documented);
  restart-controller pre-auth skip.
- Persistence/restart semantics: unchanged (history-backed locks and
  recovery live in `action_forms`, untouched).

## Tests and verification

- New `test_action_form_routes.py`: 9/9 pass pre-move AND post-move.
- Retained: `test_action_forms.py` (domain incl. tampered-spec,
  single-bubble-writer — repaired pin) and
  `test_action_form_process_restart.py` (live-server HMAC/restart
  recovery) — pass.
- Focused combined: action-form routes + domain suites 48 passed,
  1 skipped.
- Broad: `.venv/bin/python -m pytest -q` → **1,749 passed, 28 failed,
  60 skipped**; the 28 failures are byte-identical to the Phase 0/1 and
  Slice 1 baseline lists (verified via `diff`) — all unrelated.
- Note: running `test_action_form_process_restart.py` immediately before
  `test_http_authz.py` in one command pollutes 8 auth tests; verified
  pre-existing on the clean baseline via `git stash` (same 8 fail
  without my changes). Full-suite order is unaffected (http_authz green
  there).
- `ast.parse` clean; import check confirms no monolith import; grep
  confirms no handler remnants in the monolith.
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip.

## Metrics

| Metric | Before (`805b774c`) | After | Method |
|---|---|---|---|
| `web_chat_api.py` lines | 12,429 | 12,152 (−277 net) | `wc -l` |
| `/api/action-form/*` handlers owned by monolith | 4 | 0 | grep |
| New owned modules | — | `api/action_form_routes.py` (4 routes) | — |
| Transport tests | 0 (domain + restart only) | +9 route/auth/shape/recovery tests | pytest |
| Full suite | 1,740 / 28 / 60 | 1,749 / 28 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. `wca.get_auth_db` / `wca.project_manager` references remain for
   non-extracted users — expected; later slices narrow them.
2. Test-ordering pollution (restart suite → auth suite in one command)
   is pre-existing, unrelated, and not worsened; full-suite order is green.
3. Git routes (~21 handlers, needs a service layer first) explicitly
   deferred — not started in this slice.
4. The 28 baseline failures are untouched and unrelated.

## Diff summary

- Added: `src/api/action_form_routes.py`,
  `src/tests/test_action_form_routes.py`.
- Modified: `src/api/web_chat_api.py` (−277 net: −284 handlers,
  +7 registration), `src/tests/test_action_forms.py` (one pin repair).
- Deleted: no files.
- `git status --short` before commit: 3 modified + 2 new paths (above).

## External Review Summary

1. **What changed architecturally?** Action-form HTTP transport moved
   from the Flask monolith to an owned `action_forms_bp` blueprint; the
   monolith now only composes it. Domain, HMAC model, and recovery
   mechanics did not move.
2. **What behavior intentionally changed?** Nothing (one
   import-pattern normalization: two monolith-namespace globals now
   resolve via the same lazy-import pattern the file already used;
   all patch points preserved).
3. **What behavior should be identical?** All 4 route paths/methods,
   auth matrices, toast/error shapes, status codes, HMAC
   accept/reject, history recovery, one-shot locks, resume inject
   semantics, restart-controller skip.
4. **What remains coupled or messy?** Git + tasks still inline;
   monolith retains `get_auth_db`/`project_manager` refs for remaining
   users; 28 unrelated baseline failures preserved; pre-existing
   restart→auth test-ordering pollution noted.
5. **What should be reviewed before the next slice?** The ownership
   contract header in `action_form_routes.py`; confirmation that Git
   (service layer first) is the correct next slice.
6. **Is the next slice safe to begin?** This slice is self-contained
   (no chat/router/workers/git touched; failures identical to
   baseline). Do NOT begin the Git extraction in this track until this
   review is approved.

---

# Phase 2 — Slice 3A: Git Service Boundary

## Phase status

- Slice: Phase 2 Slice 3A — Git service boundary (`api.git_service`).
  Routes intentionally NOT moved (transport stays in the monolith).
- Git baseline before work: `5af5efd7` ("Phase 2 slice 2: action-form
  HTTP transport extraction to owned blueprint"), clean tree.
- Git commit after work: the single `Phase 2 slice 3A: git service
  boundary` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. The Git
  Blueprint/route extraction is explicitly NOT started.

## Original problem

The Git area (~21 handlers) combined two strata: newer handlers already
delegating to the owned `scripts.utilities.git_pending_changes` /
`git_graph` service layer, and eight legacy handlers embedding raw
subprocess calls, output parsing, and status/diff/commit/pull/push/branch
behavior directly in the Flask monolith. Any agent fixing Git behavior had
to work inside the 12.1k-line composition root, and the eventual Git
Blueprint had no service to be thin over.

## Dependency map (measured pre-move)

- 20 unique `/api/git/*` paths, 21 handlers (19 GET/POST routes + the
  duplicate below). Auth: all reads `authenticated_required`, all
  mutations `owner_required` (verified programmatically).
- Already serviced (delegate to utils, untouched this slice): `repos`,
  `pending-changes`, `open-diff`, `open-file`, `pending-diff`,
  `commit_pending`, `ignore`, `suggest-commit-message`, `graph`,
  `commit detail`, `commit file-diff`.
- Inline legacy (rewired this slice): `status`, `branches`, `commits`,
  `files`, `commit diff`, `commit` (legacy), `pull`, `branch`, plus the
  branch-fallback + push-exec inside `push`.
- Shared helpers used by non-Git code (not moved, already owned):
  `git_run`, `git_push_target/command`, `resolve_allowed_project_cwd`,
  `resolve_allowed_repo_root`, `sanitize_git_output`,
  `collect_project_pending_changes`, `commit_pending_changes`
  (consumers: agent harness cwd, supervised evidence, device workers,
  edit attribution, cuttle jobs).
- Frontend callers (unchanged paths/shapes): `git_ui.js` (status, push,
  pull, files, commits, branches, commit, branch), `git_graph_page.js`
  (graph, repos), `git_commit_viewer.js` (commit diff, pending-diff),
  `pending_changes_panel.js` (pending-changes, ignore, commit, suggest).
- Notable discovery: **two handlers share `POST /api/git/commit`** —
  `git_commit_pending` (registered first, always serves) shadows the
  legacy `git_commit` (unreachable dead route). Pinned by test; removal
  is a separate verified decision, not this slice.

## Service-boundary plan (executed)

New module `src/api/git_service.py` (501 L): `GitError` (fully formatted
handler-compatible messages) + `NotARepositoryError` (the one 400 case);
`resolve_repo_cwd`, `repo_work_tree`, `repo_status`, `list_branches`,
`list_commits`, `list_files`, `commit_diff`, `commit_changes`,
`pull_repo`, `current_branch_name`, `push_repo`, `branch_operation`.
Rules enforced: no Flask/`request`/`jsonify`, no `web_chat_api` import
(verified), no HTTP globals; narrow `cwd`-in/data-out operations;
project allowlisting stays transport-side (handlers still resolve via
`resolve_allowed_project_cwd`); `timeout=None` preserves legacy
wait-forever semantics; `push_repo` uses the shared `git_push_command`
argv verbatim (upstream/HEAD fallbacks, credential-helper flags).

## Changes made

- **Added `src/api/git_service.py`** with the operations above, ported
  line-for-line from the inline handlers including two embedded quirks
  (found during characterization, pinned by tests):
  (a) `stdout.strip()` eats the first porcelain line's leading status
  space, so a worktree-modified first file counts `staged=1` (status)
  and misses the files status map (reads `clean`, invisible to the
  status filter); (b) staged content (no leading space) parses normally.
- **Rewired 8 handlers + push internals** in `web_chat_api.py` to thin
  shells: auth + request parsing/validation + project-manager cwd fetch
  + service call + unchanged response shaping + `GitError` mapping to
  the identical strings. Monolith: 12,152 → 11,801 lines (−351 net).
- Deliberate micro-delta (documented): `commit_changes` captures `git
  add` stderr, so an add-failure now reports the real error instead of
  the legacy `Git commit failed: None` (add ran uncaptured under
  `check=True`). Commit-step messages are byte-identical.
- Legacy `git_commit` kept calling the service with a shadowing NOTE
  (still registered, still shadowed, still unserved).
- **Tests:** new `src/tests/test_git_service.py` (12 tests): 8 HTTP
  parity tests written pre-move against the monolith (auth matrix,
  status/branches/commits/files/commit-diff shapes, branch lifecycle +
  validation, local-remote pull/push round-trip, shadow pin, non-repo
  400/500s) green both sides; 4 service unit tests against real tmp
  repos (status/quirks, branches/commits/files, diff+commit_changes,
  pull/push/branch incl. error paths). Quirk pins carry comments.
- Moved vs deleted: subprocess behavior relocated (moved); no routes,
  shapes, codes, or auth touched; no obsolete code removed.

## Architecture after

```
web_chat_api.py (transport: auth/parse/shape, 8 thin git handlers)
      │  uses-service                    │  UTIL-gpc/graph (as before)
      ▼                                  ▼
api.git_service (pure git)    scripts.utilities.git_pending_changes
      │  reuses runner/env              (shared, pre-existing)
      └──────────────▶ git_run, git_push_command, _git_commit_env
```

One-way dependencies throughout; no cycles (utils import no `api`
modules at top level). The future Git Blueprint can now wrap
`git_service` + utils without touching subprocess code.

## Dependencies and state

- Removed: all inline `subprocess` use from Git handlers (verified zero
  remaining); duplicated cwd-resolution blocks now flow through
  `resolve_repo_cwd`.
- Introduced: `api.git_service` (no new runtime deps; no shared state —
  all functions are pure over explicit `cwd`).
- Reverse deps: none existed on the inline bodies; service is newly
  importable by HTTP + future internal callers alike.
- Restart/persistence semantics: none exist in this layer (stateless
  subprocess calls); unchanged.
- Allowlisting/safety: unchanged and still enforced transport-side
  (`resolve_allowed_project_cwd/repo_root` untouched in serviced
  handlers; `resolve_repo_cwd` only maps an already-resolved project).

## Tests and verification

- New `test_git_service.py`: 8/8 HTTP parity green pre-move AND
  post-rewire; 4/4 service unit tests green.
- Neighbors: `test_git_pending_changes.py` 41/41 combined with the above;
  `test_http_authz.py` + `test_commit_message_suggester.py` green (2
  pre-existing project_chip failures only).
- Broad: `.venv/bin/python -m pytest -q` → **1,761 passed, 28 failed,
  60 skipped**; the 28 failures byte-identical to the established
  baseline (verified via `diff`).
- `ast.parse` clean; grep confirms zero inline subprocess in git
  handlers and no Flask/monolith imports in the service.
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip.

## Metrics

| Metric | Before (`5af5efd7`) | After | Method |
|---|---|---|---|
| `web_chat_api.py` lines | 12,152 | 11,801 (−351 net this slice) | `wc -l` |
| Inline-subprocess git handlers | 8 | 0 | span grep |
| New owned modules | — | `api.git_service` (12 ops) | — |
| Git behavior tests | pending-changes/auth only | +12 parity + unit tests | pytest |
| Full suite | 1,749 / 28 / 60 | 1,761 / 28 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Flask-specific Git code remaining (by design): all 21 route
   registrations, auth decorators, request validation/clamps, allowlist
   resolution, and rich response shaping (esp. push Gitea hints) — the
   future Blueprint slice moves these onto `git_service`.
2. Shadowed legacy `git_commit` route: removal needs frontend-closure
   verification (callers always reach `git_commit_pending` today);
   proposed for the Blueprint slice, not here.
3. `wca.project_manager`/`get_auth_db` references remain for
   non-extracted users — expected.
4. `timeout=None` preserves legacy hangs deliberately; a future slice
   may introduce bounded timeouts as an explicit reliability change.
5. The 28 baseline failures are untouched and unrelated.

## Diff summary

- Added: `src/api/git_service.py` (501 L),
  `src/tests/test_git_service.py` (12 tests).
- Modified: `src/api/web_chat_api.py` (−351 net: 8 handlers thinned +
  push internals; ~108 added glue).
- Deleted: no files.
- `git status --short` before commit: 1 modified + 2 new paths (above).

## External Review Summary

1. **What changed architecturally?** Inline Git subprocess behavior
   moved from 8 Flask handlers into owned `api.git_service`; handlers
   are now thin transport over the service (+ pre-existing utils).
   No routes moved.
2. **What behavior intentionally changed?** One micro-delta: failed
   `git add` now reports real stderr instead of `None`. Everything
   else byte-identical (including two `strip()` parsing quirks, pinned).
3. **What behavior should be identical?** All 20 route paths/methods/
   auth/shapes/codes; status/branch/commit/file/diff/pull/push
   outputs; allowlisting; error strings; the shadowed legacy route
   still registers (still unserved).
4. **What remains coupled or messy?** All Git transport still inline
   (by design for 3A); shadowed legacy route kept pending verified
   removal; 28 unrelated baseline failures preserved.
5. **What should be reviewed before the Blueprint slice?** The service
   API surface in `git_service.py` (12 ops + error types); the
   `push_repo` argv handling; whether to remove the shadowed legacy
   route in the same Blueprint pass.
6. **Is the Blueprint slice safe to begin?** The service is proven
   (12 new tests + parity). Do NOT begin it in this track until this
   review is approved.

---

# Phase 2 — Slice 3B: Git HTTP Blueprint Extraction

## Phase status

- Slice: Phase 2 Slice 3B — Git HTTP transport → `api.git_routes`
  (`git_bp`); shadowed legacy commit route removed with proven closure.
- Git baseline before work: `1247461a` ("Phase 2 slice 3A: git service
  boundary extraction"), clean tree.
- Git commit after work: the single `Phase 2 slice 3B: git HTTP blueprint
  extraction` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No next
  Phase 2 domain started.

## Original problem

After Slice 3A owned all Git behavior, the 21 route registrations plus
auth/validation/shaping still lived inline in the Flask monolith (~1,400
lines), and a dead shadowed handler sat beside the live commit route. Any
agent touching Git transport still worked inside the 11.8k-line
composition root.

## Before moving routes (reconfirmed inventory)

- 20 unique `/api/git/*` paths, 21 handlers; reads
  `authenticated_required`, mutations `owner_required` (Slice 3A matrix
  re-verified unchanged).
- Delegation classes confirmed per handler: `api.git_service` (8 thinned
  handlers + push internals), `scripts.utilities.git_pending_changes`
  (repos, pending-changes, open-diff, open-file, pending-diff, commit,
  ignore, push-target/sanitize), `git_graph` utils (graph, detail,
  file-diff), `commit_message_suggester` + `inference_mode` (suggest),
  `fs_reveal` (open/reveal), `project_manager` (cwd/registry reads).
- Externals used by the moved block: `project_manager`, `request`,
  `jsonify`, `pathlib.Path` (open-file; now imported by the new module),
  inline `print`/`traceback` (kept verbatim). No `os`/`json`/`sys`/
  module-level needs beyond that (verified by scan + AST check).
- Frontend callers re-verified: `git_ui.js`, `git_graph_page.js`,
  `git_commit_viewer.js`, `pending_changes_panel.js`, `chat_page.js`,
  `app_shell.js` (pending-changes poll) — all hit paths/shapes, none
  reference handler symbols.

## Changes made

- **Added `src/api/git_routes.py`** (1,388 L): `git_bp` blueprint
  (`url_prefix="/api"`, short `/git/*` paths per the settings/projects
  precedent), 19 handlers moved (mechanical `@app.route` →
  `@git_bp.route` via scripted extraction, bodies verbatim). Module
  docstring states the transport-only ownership contract and the Phase 2
  rule (never imports `api.web_chat_api` — verified).
- **Monolith:** registers `git_bp` (same try/except pattern); deleted
  the ~1,400-line block. Zero `def git_*` remain. Monolith:
  11,801 → 10,406 lines (−1,395 net).
- **Shadowed legacy removal (dead-code cleanup, closure proven):**
  `git_commit` (legacy `POST /api/git/commit`) deleted from the moved
  set — never dispatched (first-registered `git_commit_pending` always
  wins); zero prod Python references; both frontend callers hit the path
  (served by pending before and after); legacy modal is shape-agnostic
  (`data.success` only). Companion service op `commit_changes` (sole
  caller was the dead handler) removed from `api.git_service` with its
  now-unused `_git_commit_env` import; service test now commits via git
  CLI. Test asserts exactly one `POST /api/git/commit`
  (`git.git_commit_pending`).
- **Tests:** `test_git_service.py` 12/12 (8 HTTP parity green pre-move,
  post-rewire, and post-blueprint; 4 service unit); repaired two slice
  artifacts — stub helper now covers the blueprint holder (same
  Slice-1 pattern; applied to `test_git_service._ctx` and shared
  `test_http_authz._stub_project_registry`), shadow test → single-
  registration assertion.
- Moved vs deleted: 19 handlers relocated verbatim (moved); legacy
  handler + its orphaned service op deleted as proven-dead (deleted).

## Architecture after

```
web_chat_api.py ──registers──▶ git_bp (api/git_routes.py, transport only)
                                   ├─▶ api.git_service (8 lanes + push parts)
                                   ├─▶ scripts.utilities.git_pending_changes
                                   ├─▶ git_graph utils, suggester, fs_reveal
                                   └── managers.project_manager (registry/cwd)
```

One-way dependencies throughout; blueprint endpoints carry the `git.`
prefix (`git.git_status`, …). No new shared state; allowlisting still
enforced transport-side via the untouched resolver calls.

## Dependencies and state

- Removed: all 21 Git route registrations + the dead handler from the
  composition root; orphaned `commit_changes` op from the service.
- Introduced: `api/git_routes.py` (Flask Blueprint; no new runtime
  deps). No new shared state; none moved.
- Reverse deps: none existed on moved handlers (verified); none created.
- Allowlisting/safety: byte-identical enforcement (resolver calls moved
  verbatim); quirk pins and `timeout=None` semantics untouched.
- Restart/persistence semantics: none in this layer; unchanged.

## Tests and verification

- `test_git_service.py`: 12/12 (parity green across all three states:
  monolith, service-backed, blueprint).
- `test_git_pending_changes.py` + `test_http_authz.py`: 87/87 combined
  with the above (covers Pending Changes, auth matrix, project-context
  persistence, suggester neighbors green; only pre-existing project_chip
  failures elsewhere).
- Broad: `.venv/bin/python -m pytest -q` → **1,761 passed, 28 failed,
  60 skipped**; the 28 failures byte-identical to the established
  baseline (verified via `diff`).
- `ast.parse` clean on all touched files; grep confirms zero `def git_*`
  in the monolith, no monolith import in the blueprint, exactly one
  `POST /api/git/commit`, no dangling `git_commit`/`commit_changes`
  references outside this review's historical sections.
- A mid-slice double-prefix fault (`/api/api/...`, from full-path
  decorators under `url_prefix`) was caught by the parity tests and
  fixed to short paths before verification.
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip.

## Metrics

| Metric | Before (`1247461a`) | After | Method |
|---|---|---|---|
| `web_chat_api.py` lines | 11,801 | 10,406 (−1,395) | `wc -l` |
| Git handlers owned by monolith | 20 (21 routes) | 0 | grep |
| New owned modules | — | `api/git_routes.py` (19 routes) | — |
| Dead code removed | — | legacy handler + orphaned service op | closure proof |
| Full suite | 1,761 / 28 / 60 | 1,761 / 28 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Monolith still holds `project_manager`/`get_auth_db` references for
   remaining users (chat, tasks, fs, launchers, …) — expected; later
   slices narrow them. Test stub helpers must now cover each new holder
   (established pattern, applied twice).
2. Tasks routes (`/api/tasks*`) still inline — natural next slice
   candidate alongside remaining misc extractions.
3. The 28 baseline failures are untouched and unrelated.

## Diff summary

- Added: `src/api/git_routes.py` (1,388 L).
- Modified: `src/api/web_chat_api.py` (−1,395 net: block delete +7
  registration), `src/api/git_service.py` (−48: orphaned op + import),
  `src/tests/test_git_service.py` (holder stubs, CLI commit setup,
  single-registration assertion),
  `src/tests/test_http_authz.py` (+7: holder stub).
- Deleted: no files (dead handler removed within the moved set).
- `git status --short` before commit: 4 modified + 1 new path (above).

## External Review Summary

1. **What changed architecturally?** All Git HTTP transport moved to
   owned `git_bp`; the monolith only composes it. Dead shadowed commit
   handler + orphaned service op removed with proven closure.
2. **What behavior intentionally changed?** Nothing reachable (removal
   deletes code that never served; verified unserved pre-removal).
3. **What behavior should be identical?** All 19 route paths/methods/
   auth/shapes/codes; allowlisting; quirk outputs; timeout semantics;
   frontend behavior on every Git surface.
4. **What remains coupled or messy?** Tasks + misc routes still inline;
   holder-stubbing pattern needed per extraction; 28 unrelated baseline
   failures preserved.
5. **What should be reviewed before the next domain?** The `git_bp`
   ownership header; confirmation of the next slice scope (tasks/misc
   suggested; chat lifecycle explicitly out of Phase 2).
6. **Is the next domain safe to begin?** This slice is self-contained
   (no chat/router/workers touched; failures identical to baseline).
   Do NOT begin it in this track until this review is approved.

---

# Phase 2 — Slice 4: Tasks HTTP Transport Extraction

## Phase status

- Slice: Phase 2 Slice 4 — Tasks HTTP transport → `api.task_routes`
  (`tasks_bp`).
- Git baseline before work: `130efda7` ("Phase 2 slice 3B: git HTTP
  blueprint extraction"), clean tree.
- Git commit after work: the single `Phase 2 slice 4: tasks HTTP
  transport extraction` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No
  miscellaneous-route cleanup started.

## Original problem

The 8 `/api/tasks*` handlers lived inline in the Flask monolith (~260
lines) while task behavior already had an owner (`managers.task_manager`,
SQLite-backed). Transport, validation, and one composite workflow were
fused into the composition root; the first-ever task tests had nowhere
obvious to live.

## Before implementation (inventory per route)

| Route | Auth | Manager call | Notes |
|---|---|---|---|
| `GET /api/tasks` | owner | `get_all_tasks` | returns raw list (no envelope) |
| `POST /api/tasks` | owner | `create_task` | 400 without title; 201 + defaults |
| `GET /api/tasks/<id>` | **none (public)** | `get_task` | 404 shape; pinned as-is, not fixed |
| `PUT /api/tasks/<id>` | owner | `update_task` | 400 without title; 404 |
| `DELETE /api/tasks/<id>` | owner | `delete_task` | 404 / `{success:True}` |
| `POST .../comments` | owner | `add_comment` | 400 without content; 201; orphan comments allowed (manager has no existence check — pinned) |
| `DELETE .../comments/<cid>` | owner | `delete_comment` | 404 `Comment or task not found`; 200 message |
| `POST .../close-via-commit` | owner | get/update/comment + git | NOT thin: embeds `git add`/`commit` in `os.getcwd()` (no identity, no allowlist); live caller `task_management.js:914` |

- Project/session relationships: none — `task_manager` has no project
  FK; tasks are global. Supervised overlap: none — supervised routes
  (`/api/supervised/...`) use `supervised.store`, untouched and
  explicitly excluded.
- Reverse deps on handler symbols: none (verified). Frontend callers:
  `task_management.js` only; shapes preserved.
- Tests pre-existing: none (first task tests written in this slice).

## Changes made

- **Added `src/api/task_routes.py`** (285 L): `tasks_bp` blueprint
  (`url_prefix="/api"`, short `/tasks/*` paths), 7 thin handlers moved
  verbatim (lazy `task_manager` imports kept, so existing patch points
  hold). Module docstring states the transport-only contract and the
  Phase 2 rule (never imports `api.web_chat_api` — verified).
- **close-via-commit** moved with its orchestration shape intact; its
  git step delegates to new `git_service.stage_and_commit` (frozen
  legacy semantics: raw `files` string split, `'.'` stages all,
  process cwd passed by the handler, bare environment/identity,
  `Git command failed: <str(exc)>` errors). Not a redesign: the
  `os.getcwd()` semantic and missing identity are pinned quirks for a
  later cleanup, not fixed here.
- **Monolith:** registers `tasks_bp`; deleted the ~260-line block.
  Remaining `*task*` defs are other domains (page server, 2 supervised
  routes) and stay. Monolith: 10,406 → 10,152 lines (−254 net).
- **Tests:** new `src/tests/test_task_routes.py` (5 tests, written
  pre-move, green both sides): route-registration contract; anon
  401/guest 403 matrix (+ public-GET pin); CRUD shapes incl. 201
  defaults and 400/404s; comment shapes incl. orphan pin; close flow
  (400/404/success, `Closes <taskId>` in body, status Closed) in a real
  repo via monkeypatched cwd.
- Moved vs deleted: handlers relocated (moved); nothing deleted.

## Architecture after

```
web_chat_api.py ──registers──▶ tasks_bp (api/task_routes.py)
                                   ├─▶ managers.task_manager (SQLite)
                                   └──▶ api.git_service.stage_and_commit
                                        (close-via-commit git step only)
```

One-way dependencies; no new shared state; lazy manager imports
preserved so `managers.task_manager.task_manager` remains the single
stub point (no holder-split: handlers bind at request time).

## Dependencies and state

- Removed: 8 route registrations from the composition root.
- Introduced: `api/task_routes` + one narrow `git_service` op (sole
  caller: the moved route). No new runtime deps, no new mutable state.
- Reverse deps: none existed; none created. Supervised task system
  untouched.
- Persistence: unchanged (`tasks.db` via manager; per-test isolated DBs
  in the new tests).
- Auth/ownership: byte-identical matrix, including the public
  single-task GET (documented, not altered).

## Tests and verification

- New `test_task_routes.py`: 5/5 pre-move AND post-move.
- Neighbors: project routes + auth + git service suites 62/62 combined;
  supervised suites untouched and unaffected (full-suite diff clean).
- Broad: `.venv/bin/python -m pytest -q` → **1,766 passed, 28 failed,
  60 skipped**; the 28 failures byte-identical to the established
  baseline (verified via `diff`).
- `ast.parse` clean; grep confirms no task handlers in the monolith, no
  monolith import in the blueprint, single registration per route.
- A mid-slice double-prefix fault (`/api/api/...`) was caught by the
  new contract test and fixed to short paths (same lesson as Slice 3B).
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip.

## Metrics

| Metric | Before (`130efda7`) | After | Method |
|---|---|---|---|
| `web_chat_api.py` lines | 10,406 | 10,152 (−254) | `wc -l` |
| Task handlers owned by monolith | 8 | 0 | grep |
| New owned modules | — | `api/task_routes.py` (8 routes) | — |
| Task behavior tests | 0 | +5 contract/auth/shape/flow tests | pytest |
| Full suite | 1,761 / 28 / 60 | 1,766 / 28 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. `close-via-commit` frozen quirks (process-cwd commits, no identity,
   string `files.split()`, orphan comments, public single-task GET)
   are pinned tech debt for a later task-domain cleanup — not this slice.
2. Supervised task routes (`/api/supervised/...`) are a separate domain
   and stay; similar names must not merge ownership (verified disjoint).
3. The 28 baseline failures are untouched and unrelated.

## Diff summary

- Added: `src/api/task_routes.py` (285 L),
  `src/tests/test_task_routes.py` (5 tests).
- Modified: `src/api/web_chat_api.py` (−254 net: block delete +7
  registration), `src/api/git_service.py` (+27: one narrow op).
- Deleted: no files.
- `git status --short` before commit: 3 modified + 2 new paths (above).

## External Review Summary

1. **What changed architecturally?** Task HTTP transport moved to
   owned `tasks_bp`; the monolith only composes it. One composite
   workflow kept its shape with its git step delegated to a narrow,
   frozen-semantics service op.
2. **What behavior intentionally changed?** Nothing (one addition:
   a service op that did not exist; its semantics equal the inline
   code it replaces).
3. **What behavior should be identical?** All 8 route paths/methods/
   auth/shapes/codes; CRUD defaults; comment and close flows;
   error strings including git failure mapping.
4. **What remains coupled or messy?** Misc routes still inline;
   close-via-commit quirks pinned as debt; supervised system separate;
   28 unrelated baseline failures preserved.
5. **What should be reviewed before reassessment?** The `tasks_bp`
   ownership header; whether Phase 2 closes here or one more bounded
   slice (tasks-adjacent misc) is justified — chat lifecycle stays out.
6. **Is anything else safe to begin?** This slice is self-contained
   (no chat/router/workers/supervised touched; failures identical to
   baseline). Do NOT begin misc cleanup in this track until review
   decides Phase 2's end.

---

# Phase 2 — Closure Summary

Phase 2 is closed by external direction. No miscellaneous-route
extraction was performed; the phase ends with the four completed slices
above. Status recorded 2026-10-01 on commit `9ffd062e` (clean tree).

## Slices delivered

| Slice | Owner created | Monolith gave up | Tests added |
|---|---|---|---|
| 1 — Projects transport | `api/project_routes` (`projects_bp`, 10 routes) | ~300 lines | 4 contract/auth/shape/guard |
| 2 — Action Forms transport | `api/action_form_routes` (`action_forms_bp`, 4 routes) | ~280 lines | 9 transport/auth/recovery |
| 3A — Git service boundary | `api.git_service` (12 pure ops, no Flask) | 0 routes (behavior only) | 4 service unit (real repos) |
| 3B — Git transport | `api.git_routes` (`git_bp`, 19 routes) | ~1,400 lines | 8 HTTP parity |
| 4 — Tasks transport | `api.task_routes` (`tasks_bp`, 8 routes) | ~260 lines | 5 contract/auth/shape/flow |

Dead code removed with proven closure along the way: shadowed legacy
`POST /api/git/commit` handler + orphaned `commit_changes` service op
(Slice 3B). Documented tech debt deliberately NOT changed: public task
GET, process-CWD close commits, missing git identity there, raw
`files.split()`, orphan comments, porcelain `strip()` quirks,
`timeout=None`, restart-controller pre-auth skip.

## `web_chat_api.py` Phase-0 → Phase-2 size change

- 12,732 → 10,152 lines (−2,580, −20%). Route decorators on the
  monolith app: 184 → 139 (−45).
- Composition-root shape now: single global `app` + TLS/CORS/limiter
  init + 5 owned blueprints (`auth`, `workers`, `dashboards`,
  `settings`, `projects`, `action_forms`, `git`, `tasks` — auth/workers/
  dashboards/settings predate Phase 2) + `register_*` route groups +
  remaining inline handlers (chat core, sessions, shell/panes, launchers,
  home-automation, mobile, media, infra, supervised thin handlers).
- Reduction was never the gate: every slice moved or created ownership
  (or removed proven-dead code); line deltas are reported as evidence,
  not success criteria.

## Ownership/dependency improvements

- Direction is one-way everywhere new: blueprints → managers/services/
  utils; `git_service` → shared runner/env helpers. Verified no
  `web_chat_api` import in any new module.
- Shared test-stub pattern established: stub helpers cover each holder
  module holding a manager reference (`wca` + blueprints).
- Lazy function-level imports preserved where handlers already used
  them, keeping existing test patch points working.
- `chat_page.js` iframe messaging and all frontend contracts untouched
  throughout Phase 2 (backend paths/shapes/codes frozen per slice).

## Test-baseline preservation

- Full suite at closure: **1,766 passed, 28 failed, 60 skipped** —
  failures byte-identical to the Phase 0 baseline list at every slice
  gate (verified via `diff` four times). Slice test inventory added
  across Phase 2: 4 + 9 + 12 + 5 = 30 new tests, all green.
- Two pre-existing test-hygiene findings recorded (not fixed — out of
  scope): restart-suite → auth-suite ordering pollution in ad-hoc
  combined runs (full-suite order green), and holder-split stubbing
  noted as a future dependency-injection cleanup candidate.

## Intentionally deferred backend concerns

- Remaining inline handlers (chat core/turn coordinator, sessions,
  shell/panes, launchers, home-automation, mobile, media, infra,
  supervised thin handlers, tasks-adjacent misc, `/api/project-commands`
  cwd-resolver lane, fs routes) — future phases, not Phase 2.
- Git reliability/behavior cleanups (bounded timeouts, quirk fixes,
  shadow-route-adjacent frontend dead ends) — later cleanup with
  explicit product review.
- Task-domain quirks (public GET, process-CWD commits, identity,
  string split, orphans) — explicit security/reliability review later.
- Chat lifecycle / turn coordinator extraction — explicitly out of
  Phase 2 (Phases 4–5 territory).

## Remaining reverse dependencies (Phase 0 list, re-verified)

All 11 Phase 0 prod importers still reach into the monolith (plus one
missed in Phase 0 now recorded): `agent_harness/kernel`
(`_default_chat_cwd`), `internal_http` (`app.test_client`),
`chat_delivery` (live-status), `auth_api` (live session ids),
`subagents/turns` + `subagents/identity` (message/badge metadata),
`supervised/orchestrator` + `supervised/adapters` + `dispatch`
(runners/status), `chat_status_phases` (emit), `chat_run_registry`
(clear status), and `doctor.py` (importability health-check only —
not a real dependency). None were widened by Phase 2; narrowing them
is Phase 4 work, gated on the same per-slice evidence standard.

---

# Phase 3 — Slice 1: Project Context Domain (chat_page.js)

## Phase status

- Slice: Phase 3 Slice 1 — project-context pure domain →
  `src/web/js/chat_project.js` (`window.CuttleChatProject`).
- Git baseline before work: `fad46b4c` ("Phase 2 closure summary"),
  clean tree.
- Git commit after work: the single `Phase 3 slice 1: chat project
  context domain` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No further
  chat-page domains started (streaming, messages/history, composer,
  slash registry, agent/model controls, attachments, action forms
  untouched).

## Original problem

`chat_page.js` (26.7k lines, single IIFE) owned project-context
resolution — the reconcile priority chain (server → prefs → stored →
keep-unsaved-pick → backfills → default), registry lookups, outbound
stamping, and message-metadata mapping — as closures over chat globals.
This logic already caused regressions (CH-000204 snap-back, Escape
Purgatory), yet its only tests extracted source slices by comment
markers, which rotted: both chip tests failed at baseline on a stale
end marker, never executing.

## Before implementation (inventory)

- 97 name-matched functions triaged; true slice: `findProjectById`,
  `projectObjectFromStoredFields`, `projectFieldsFromServerSession`,
  `resolveSessionProjectInfo` (history use, kept in place),
  `reconcileChatProject`, `resolveOutboundProject`,
  `applyOutboundProjectToRequest`, `projectFromPath`,
  `projectFromMessageOpts`, `findDefaultProject`,
  `currentProjectPathKey`, `newChatForProject` fallback chain,
  starred-path normalize.
- False friends explicitly excluded: attachment drafts (`take/…Pending
  Attachments`), follow-up queue (`pendingSlash…`, `collectPendingResult`),
  history grouping/filtering, starred settings IO, slash palette/dispatch,
  pending-diff modal render (~500 L, rendering-last), git push modal and
  commit-viewer wiring, chip HTML render, project selector/display DOM,
  persistence writes, shell postMessage, palette refresh, fetch transport.
- Already-extracted companions (not re-touched):
  `pending_changes_panel.js` (`window.CuttlePendingChangesPanel`,
  chat_page keeps a thin `pendingChangesCtl` adapter),
  `git_ui.js`, `git_commit_viewer.js`.
- Shared bindings the slice reads: `projects`, `currentProject`,
  `currentSessionId`, `lastHydratedSessionProject` singletons; session
  helpers (`getSessionPrefs`, `toAuthDbSessionId`, `sessionIdsEqual`,
  `sessionHasStoredProject`, `authSessionListHas`,
  `findAuthServerSession`); starred prefs IO; render/persist/palette
  callbacks. Iframe/shell/backend contracts: `cuttle-pending-project`
  postMessage (stays), `/api/projects` + `/api/projects/<id>/switch`
  fetches (stay), `/api/auth/sessions PATCH` persist (stays).
- `window.*` exposure: only `newChatForProject` + `switchChatProject`
  (orchestration, stay). No moved function was externally exposed.

## Changes made

- **Added `src/web/js/chat_project.js`** (359 L): pure lookups
  (`findProjectById/ByName/ByPath`, `findProject` fallback chain,
  `findDefaultProject` with injected starred, `normalizeProjectPath`),
  mappers (`projectFieldsFromServerSession`,
  `projectObjectFromStoredFields`, `projectFromPath`,
  `projectFromMessageOpts`), decision core
  (`resolveChatProject(inputs)` — the reconcile priority chain over
  explicit `{projects, currentSessionId, currentProject, serverFields,
  prefs, stored, starred}`), `rebindProjectToRegistry` (stale
  name/path repair with `{proj, changed}`), `applyOutboundProjectToRequest`,
  `currentProjectPathKey`. No DOM/window/localStorage/fetch; node-
  requirable (footer uses `globalThis` directly — TDZ-proof for
  importers that declare a lexical `window`).
- **chat_page.js keeps**: singletons, session/prefs/server IO gathering,
  DOM, persistence writes, slash/palette/history/shell/push/fetch, and
  thin orchestration shells (`reconcileChatProject` gathers →
  `resolveChatProject` → applies + unchanged persist/render tails;
  `resolveOutboundProject` keeps the welcome-pick guard, delegates the
  rebind; `newChatForProject` delegates its fallback chain) plus
  singleton-binding wrappers (`findProjectById`,
  `projectFieldsFromServerSession`, `findDefaultProject`,
  `projectFromPath`, `projectFromMessageOpts`) so ~30 unrelated call
  sites don't churn. Deleted outright: `projectObjectFromStoredFields`
  (sole caller moved into the module).
- chat_page.js: 26,700 → 26,503 lines (−197 net).
- `chat_page.html`: `chat_project.js` script tag before `chat_page.js`
  (both `?v=20261001slice1` cache-busted).
- **Tests:** new `src/tests/test_chat_project.py` (5 tests,
  node-executed against the real module): lookups/mappers, full
  reconcile priority matrix, rebind/outbound, message-opts mapping,
  parse check. Repaired `test_project_chip_persistence.py` to
  `require()` the module instead of source-slicing moved code (slicing
  is what rotted it), repaired its stale fixture name, and added the
  missing harness stubs the old slices never provided — converting
  both baseline failures to passes.
- Moved vs deleted: decision/mapping logic relocated verbatim
  (moved, two fidelity fixes during porting: id-tier `hadStored`
  unconditionality, rebind persist-condition parity); dead def removed.

## Architecture after

```
chat_page.js (orchestration: gather → decide → apply/render/persist)
      │  explicit inputs / narrow wrappers
      ▼
chat_project.js (pure: lookups, resolveChatProject, rebind, outbound)
```

One-way dependency (page → namespace); module holds no state and
reads no chat globals. Pending Changes/Git UI stay in their existing
companion modules behind thin adapters.

## Dependencies and state

- Removed: chat-page closures over singletons for all moved decision
  logic; one dead def.
- Introduced: `CuttleChatProject` namespace (classic script + node
  exports; no new runtime deps). No shared state added or moved —
  singletons stay in the page and are passed explicitly.
- Reverse deps: none existed on these functions outside the page (only
  the repaired test); none created.
- Persistence/restart semantics: unchanged (all writes stay in the
  page's persist path).
- Two porting fidelity items preserved exactly (hadStored-on-miss,
  rebind persist conditions); verified by the matrix tests.

## Tests and verification

- New `test_chat_project.py`: 5/5 (covers every preserved contract in
  the slice brief: command-adjacent resolution, chip inputs, picker
  registry, new/existing-chat behavior, per-chat persistence inputs,
  switching inputs, outbound/CWD stamping).
- Repaired chip tests: 2/2 pass (were baseline failures on marker rot;
  now execute the CH-000204 scenario against module + mirrored shells).
- Neighbors: project routes + auth + git service + attachments suites
  green except 3 pre-existing attachments failures.
- Broad: `.venv/bin/python -m pytest -q` → **1,773 passed, 26 failed,
  60 skipped**; the 26 failures are the baseline 28 minus the 2
  repaired chip tests (verified via `diff` — strictly fewer, no new).
- `node --check` clean on both JS files.
- Manual workflows: none applicable beyond prior passes (no UI changed;
  DOM-level project gestures were covered by the Phase 1 browser pass
  on the same surfaces). Not exercised: live Flask boot, chat round-trip.

## Metrics

| Metric | Before (`fad46b4c`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 26,700 | 26,503 (−197) | `wc -l` |
| Project decision fns needing chat globals | ~14 (all) | 0 in module (explicit inputs) | grep |
| Project behavior tests | 0 green (2 rotten) | +5 module +2 repaired | pytest |
| Full suite | 1,766 / 28 / 60 | 1,773 / 26 (28 minus 2 fixed) / 60 | pytest + diff |

## Remaining concerns

1. Rendering, persistence writes, slash, palette, history, shell/push/
   fetch, starred IO, and the pending-diff modal stay in the page
   (correct per phase plan: rendering/DOM last, one domain per slice).
2. The repaired chip harness still slices orchestration shells by
   markers (unavoidable until those shells decompose); markers
   re-verified unique in this slice.
3. History grouping/filtering duplicates some lookup chains inline
   (`resolveSessionProjectInfo` etc.) — future slice may route them
   through the module; left untouched to bound blast radius.
4. 26 remaining baseline failures are untouched and unrelated.

## Diff summary

- Added: `src/web/js/chat_project.js` (359 L),
  `src/tests/test_chat_project.py` (5 tests).
- Modified: `src/web/js/chat_page.js` (−197 net: delegation + one dead
  def removed), `src/web/chat_page.html` (+1 script tag, `?v` bump),
  `src/tests/test_project_chip_persistence.py` (module require + stubs).
- Deleted: no files.
- `git status --short` before commit: 3 modified + 2 new paths (above).

## External Review Summary

1. **What changed architecturally?** Project-context decision logic
   moved from the chat-page IIFE to owned pure `chat_project.js`;
   the page keeps singletons, IO, DOM, and orchestration shells.
2. **What behavior intentionally changed?** Nothing (two porting
   details matched to the original: unconditional id-tier `hadStored`,
   rebind persist parity).
3. **What behavior should be identical?** Reconcile outcomes for every
   session state, outbound stamping, chip inputs, picker/new-chat
   fallback, message metadata mapping.
4. **What remains coupled or messy?** All rendering/persistence/slash/
   history/shell/push/fetch in the page; chip harness still slices
   orchestration shells; 26 unrelated baseline failures remain.
5. **What should be reviewed before the next chat domain?** The
   `resolveChatProject` input contract (gathering stays in the page by
   design); whether the next slice is Pending Changes modal, slash
   registry, or composer.
6. **Is the next domain safe to begin?** This slice is self-contained
   (no streaming/messages/composer/agent/attachment/action-form code
   touched; failures strictly decreased). Do NOT continue in this track
   until this review is approved.

---

# Phase 3 — Slice 2: Slash Command Domain (chat_page.js)

## Phase status

- Slice: Phase 3 Slice 2 — slash registry, parsing, matching, and
  sticky/starred decisions → `src/web/js/chat_slash.js`
  (`window.CuttleChatSlash`).
- Git baseline before work: `21c071d5` ("Phase 3 slice 1: chat project
  context domain"), clean tree.
- Git commit after work: the single `Phase 3 slice 2: slash command
  domain` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No further
  chat-page domains started (palette DOM/rendering, composer send flow,
  streaming, messages/history, agent/model controls, attachments, action
  forms untouched).

## Original problem

`chat_page.js` owned the entire slash-command decision layer — the
command registries, stored-message parsing, palette matching, sticky
badge detection, starred resolution, project-command merging, native
control detection, and chip classification — as closures over page
globals. Command behavior could only be tested by slicing source text
out of the 26k-line page, and several suites did exactly that (brittle
marker coupling across ~10 test files).

## Before implementation (10-class inventory)

146 name-matched functions triaged (26,503-line page, 933 named fns):

1. **Definition/registry** (moved as data): `SLASH_COMMANDS`,
   `CURSOR_AGENT_SLASH_COMMANDS`, `HARNESS_USAGE_SLASH_BY_AGENT`,
   `HARNESS_COST_AGENT_LABELS`, `TITLE_SLASH_SKIP`.
2. **Parsing/normalization** (moved): `parseStoredSlashCommandHead/
   Message`, `parseProjectOrGenericSlashHead`, `parseTitleSlashChips`,
   `titleChipKey`, `normalizeSlashCommandStored`,
   `slashCommandMetaFromUserMessage`.
3. **Matching/search/filter** (moved pure cores): filter tokens,
   haystack, matches, category label, type bucket/badge, `starredRank`
   (nested copy now delegates; assembly/orchestration stays).
4. **Metadata** (moved): chip classifiers (`isStickyAgentChip` + 5
   per-agent, nested ×2, header, cursor-related), `sortChipsAgentThen-
   Command` (stable partition, not a label sort), `composerChipAgentId`,
   `composerChipRemovalIndexes`.
5. **Sticky/starred state** (moved decisions; IO stays):
   `getStickySlashCommandFromMessage`, `isStickySlashAssistantFailure`,
   `stickyAgentOverrideForRequest` (explicit inputs),
   `inferStickyChipsFromUserMessages`, `stickyChipsFromAssistantSlash`,
   `hasStickyAgentChip`, `activeStickyAgentChip`,
   `isSlashCommandStarred`, `starredStickyChips`, registry merge +
   mode gating (`mergeHarnessAgentsIntoSlashCommands`,
   `slashCommandsForCurrentMode`), project builders.
6. **Project-command integration** (moved pure builders):
   `buildProjectPaletteItems`, `buildProjectCommandPaletteItems`
   (fetch stays).
7. **Dispatch/action selection**: `applySlashSelection*`, compose/clear/
   typing orchestration — stay (composer/DOM-coupled). Pure dispatch
   surface: `isNativeControlCommand` (moved).
8. **Palette DOM/rendering**: render/menu/show/hide/keydown/compact-
   labels/history-palette/filter-bar — stay.
9. **Settings IO**: starred prefs read/write/hydrate, model loaders,
   project-commands fetch, harness-agents fetch, persist/restore sticky
   — stay.
10. **False friends** (verified out of scope): history starred chats,
    starred-project settings, agent model/effort builders + loaders,
    context gauge, history search palette, supervised dispatch,
    pipeline/skill builders, restart palette items, button-click
    parsing, form-reply rendering.

Shared bindings the slice reads (now explicit inputs or wrapper-bound):
registries, `slashCtx` chips, inference mode, starred prefs, supplement
lists, projects list, pipeline/model resolvers (injected callbacks).
No moved function was exposed on `window.*`.

## Changes made

- **Added `src/web/js/chat_slash.js`** (1,110 L): registry data +
  ~45 pure functions behind `CuttleChatSlash` (classic script + node
  exports; footer uses TDZ-proof `globalThis`). Pipeline/model
  resolvers inject via `deps`; everything else is explicit arguments.
- **chat_page.js keeps**: palette assembly/orchestration (incl.
  `filterSlashPaletteItems`, gated builders, `pruneCloud…`), all DOM/
  events/rendering, fetch/settings IO, chip HTML, enrichers,
  send/compose orchestration, `slashCtx`, supervised dispatch, plus
  thin same-named wrappers (identical signatures where possible) so
  ~120 unrelated call sites don't churn. Deleted outright: the two
  registry consts, harness tables, agent regexes, and all moved bodies.
- chat_page.js: 26,503 → 25,704 lines (−799 net).
- `chat_page.html`: `chat_slash.js` script tag before `chat_page.js`
  (both `?v=20261001slice2` cache-busted).
- **Tests:** new `src/tests/test_chat_slash.py` (8 tests,
  node-executed against the real module): registry shape, parsing
  matrix, matching/rank, sticky decisions, starred + control,
  project merge, classifiers/removal, parse check. Repaired 14 test
  files to require the module instead of slicing moved code
  (registry reads repointed; harnesses gain one `require` line;
  delegation assertions replace body assertions where the owner moved).
- Moved vs deleted: decision logic relocated verbatim (moved, two
  fidelity corrections during porting: full `isAgentHeaderChip` tail,
  partition-not-sort); const/duplicate bodies deleted.

## Architecture after

```
chat_page.js (palette assembly, DOM/events, IO, send orchestration)
      │  explicit inputs / same-named thin wrappers
      ▼
chat_slash.js (registry + parse + match + sticky/starred + classify)
```

One-way dependency (page → namespace); module holds no state and
reads no page globals. Palette filter assembly stays because it
composes 15+ live builders (agent models, projects, restart) that
belong to other domains.

## Dependencies and state

- Removed: chat-page closures over singletons for all moved decision
  logic; duplicated registry reads now flow through one definition.
- Introduced: `CuttleChatSlash` namespace (classic script + node
  exports; no new runtime deps). No shared state added or moved.
- Reverse deps: none existed outside the page (only repaired tests);
  none created.
- Persistence/settings semantics: unchanged (all IO stays in the page).
- Three porting fidelity items verified by differential proof (below):
  full classifier tails, stable-partition sort, plus injected-resolver
  parity for pipeline/model branches.

## Tests and verification

- New `test_chat_slash.py`: 8/8 (covers every preserved contract in
  the slice brief: parsing, exact/prefix matching, filtering, ordering
  keys, sticky/starred resolution, project merge, native control,
  classifiers, removal, empty-input edges).
- Differential proof (scratch `/tmp/slash_diff_probe.js`, not
  committed): HEAD chat_page functions vs new module over a 26-case
  battery (messages × chips × filters × registries) — **zero
  mismatches** after aligning probe inputs (several initial diffs were
  probe artifacts: stub ordering, global-vs-explicit inputs).
- Repaired suites: all 10 slash-adjacent test files green (244/244 in
  the focused run), including 2 previously-passing suites that needed
  no changes and 5 newly discovered slicing suites repaired
  (composer-removal, history-styling, muse-palette, supervised flags,
  plus cost/usage/router/badge/bare-sticky/restart/selection/starred/
  bubble).
- Broad: `.venv/bin/python -m pytest -q` → **1,781 passed, 26 failed,
  60 skipped**; the 26 failures byte-identical to the Slice 1 baseline
  (verified via `diff` — no new failures).
- `node --check` clean on all touched JS.
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip.

## Metrics

| Metric | Before (`21c071d5`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 26,503 | 25,704 (−799) | `wc -l` |
| Slash decision fns needing page globals | ~45 (all) | 0 in module (explicit inputs) | grep |
| Slash behavior tests | string pins + slices | +8 module tests; 14 files repaired to require | pytest |
| Full suite | 1,773 / 26 / 60 | 1,781 / 26 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Palette assembly, DOM/rendering, send orchestration, settings IO,
   enrichers, history palette, agent/model builders, and supervised
   dispatch stay in the page (correct per slice scope).
2. Repaired harnesses still slice orchestration shells by markers;
   markers re-verified in this slice, but each future domain move must
   re-check them (established pattern).
3. `filterSlashPaletteItems` remains the one complex assembly mixing
   15+ builders across domains — a future palette-architecture pass
   (not a single-domain slice) should address it.
4. 26 remaining baseline failures are untouched and unrelated.

## Diff summary

- Added: `src/web/js/chat_slash.js` (1,110 L),
  `src/tests/test_chat_slash.py` (8 tests).
- Modified: `src/web/js/chat_page.js` (−799 net: delegation),
  `src/web/chat_page.html` (+1 script tag, `?v` bump), 14 test files
  (require lines + registry repoints + delegation assertions).
- Deleted: no files.
- `git status --short` before commit: 15 modified + 2 new paths (above).

## External Review Summary

1. **What changed architecturally?** Slash registry, parsing,
   matching, sticky/starred decisions, project-command merging, and
   chip classification moved to owned pure `chat_slash.js`; the page
   keeps assembly, DOM, IO, and orchestration behind thin wrappers.
2. **What behavior intentionally changed?** Nothing (two porting
   corrections matched the originals exactly).
3. **What behavior should be identical?** Slash syntax, matching/
   search, sticky prefixes, starred resolution, project-command
   availability, `/project`, ordering, labels/hints, keyboard nav,
   dispatch behavior, settings persistence, new/existing-chat behavior.
4. **What remains coupled or messy?** Palette assembly over 15+
   cross-domain builders; all rendering/IO/orchestration in the page;
   harness marker-slicing for orchestration shells; 26 unrelated
   baseline failures remain.
5. **What should be reviewed before the next chat domain?** The
   decision/assembly split (assembly stays by design); whether the
   next slice is composer, streaming, or message rendering.
6. **Is the next domain safe to begin?** This slice is self-contained
   (no streaming/messages/composer-send/agent/attachment/action-form
   logic touched; failures identical to baseline). Do NOT continue in
   this track until this review is approved.

---

# Phase 3 — Slice 3: Action Forms Frontend Domain (chat_page.js)

## Phase status

- Slice: Phase 3 Slice 3 — action-forms card interpretation, watch
  interpretation, restart presentation decisions, lock/state pure logic
  → `src/web/js/chat_action_forms.js`
  (`window.CuttleChatActionForms`).
- Git baseline before work: `be530dbd` ("Phase 3 slice 2: slash command
  domain"), clean tree.
- Git commit after work: the single `Phase 3 slice 3: action-forms
  frontend domain` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No further
  chat-page domains started (composer, streaming, messages/history,
  attachments, agent/model controls untouched except the narrow
  adapter interface described below).

## Original problem

`chat_page.js` owned the entire action-forms frontend decision layer —
cancel/side-effect predicates, watch inference/keys/snapshots/terminal
detection, elapsed/bars formatting, restart progress tables/labels,
restart card linkage — as closures interleaved with DOM rendering,
fetch transport, timers, and history integration. The behavior could
only be tested by slicing source text out of the 25.7k-line page, and
the backend HMAC/recovery contract (stabilized in Phase 2 Slice 2)
had no frontend decision owner.

## Before implementation (11-class inventory)

20 name-matched bindings triaged (25,704-line page):

1. **Card data/model interpretation** (moved pure): `isExplicit-
   ActionFormCancelOption` (Q&A choices omit `action` and must NOT read
   as cancel), `actionFormHasSideEffect`, `specLooksLikeFlaskRestart`
   (form-id alone is NOT enough — guards git.push id reuse).
2. **Run/confirm behavior**: transport + orchestration — stays.
3. **Dismiss/cancel behavior**: `dismissOpenInteractiveCards`,
   `collapseLockedActionForm` (DOM + fetch) — stay.
4. **Watch-state polling**: `inferActionFormWatch` (moved),
   `safeActionFormWatchUrl` (moved), `watchRunKey` / `cardWatchBind` /
   `watchSnapshotFromSpec` / `watchIsTerminalState` (moved);
   `actionFormWatchSpec` (DOM gather), poll loops, snapshot persist —
   stay.
5. **Follow-up-message behavior**: `offerJobSuccessDiscordForm` (fetch
   + history) — stays.
6. **Restart/recovery presentation**: `RESTART_PROGRESS_PCT` /
   `RESTART_TERMINAL_STATES` (moved as owned tables),
   `restartProgressLabel` (moved), `flaskRestartFormEpoch` (moved);
   progress-bar DOM, poll orchestration, linked-event broadcast —
   stay.
7. **One-shot/locked state**: `lockWatchFormCard`,
   `unlockFlaskRestartPendingSyncCard` (DOM + spec mutation) — stay.
8. **DOM/card rendering** (`renderWatchBarsHtml`,
   `setActionFormCardProgress`, `updateRestartCardProgressBar`) — stay.
9. **HTTP transport** (`/run`, `/dismiss`, `/watch-state`,
   `/followup-message` fetches) — stays.
10. **Message-history integration** (`persistActionFormWatchSnapshot`
    server sync, `formAwaitingSessionIdFromCard`) — stays.
11. **False friends** (verified out of scope): generic confirm-button
    locking (`lockCuttleButtonsInContainer`), follow-up queue,
    activity/unread, composer, streaming, attachments.

Shared bindings the moved logic needed: none beyond explicit
arguments, except three DOM-coupled helpers that read card attributes
(`isLinkedFlaskRestartCard`, `linkedFlaskRestartFormId`,
`actionFormWatchStorageKey`) — rewired as narrow gather-then-delegate
adapters (DOM read in page, decision in module). No moved function
was exposed on `window.*`; no callers exist outside `chat_page.js`
(verified by repo-wide grep).

## Changes made

- **Added `src/web/js/chat_action_forms.js`** (297 L): 15 pure
  functions + 2 owned const tables behind `CuttleChatActionForms`
  (classic script + node exports; footer uses TDZ-proof `globalThis`).
  Two additive convenience wrappers with no page original
  (`restartProgressPercent`, `isRestartTerminalState`) are covered by
  the new tests. No `document`/`window`/`localStorage`/`fetch` in the
  module — all inputs explicit.
- **chat_page.js keeps**: all DOM rendering, fetch transport,
  timers/polling, sessionStorage IO, spec-mutation persistence,
  history integration, and orchestration, plus thin same-signature
  adapters (15 pure delegations + 3 DOM gather-then-delegate) so
  ~50 existing call sites don't churn. The 2 const tables were
  deleted and their 5 bare use-sites rewritten to qualified access
  (Slice 2 convention — no local aliases).
- chat_page.js: 25,704 → 25,591 lines (−113 net).
- `chat_page.html`: `chat_action_forms.js` script tag before
  `chat_page.js` (both `?v=20261001slice3` cache-busted).
- **Tests:** new `src/tests/test_chat_action_forms.py` (6 tests,
  node-executed against the real module — no source slicing):
  cancel/side-effect model, restart recognition + linkage, watch
  interpretation, elapsed + bars, restart progress presentation,
  module parse check.
- Moved vs deleted: decision logic relocated verbatim (moved);
  const-table duplicates and original bodies deleted (rewire asserts:
  brace balance, `}`/`;` endings, exact use-site counts 2 + 3).

## Architecture after

```
chat_page.js (rendering, fetch, timers, storage IO, history, orchestration)
      │  same-signature thin adapters / qualified table access
      ▼
chat_action_forms.js (predicates + watch interpretation + restart decisions)
```

One-way dependency (page → namespace); module holds no state and
reads no page globals. DOM-heavy rendering stays by design — moving
it would couple the module to page markup instead of plain data.

## Dependencies and state

- Removed: chat-page closures over card-spec interpretation for all
  15 moved decisions; two restart const-table definitions.
- Introduced: `CuttleChatActionForms` namespace (classic script +
  node exports; no new runtime deps). No shared state added or moved.
- Reverse deps: none existed outside the page; none created (module
  never references `chat_page.js` globals).
- Persistence/restart-sensitive state: unchanged (all sessionStorage
  IO, spec `locked`/`toast` mutation, `/watch-state` sync, and
  restart poll orchestration stay in the page). Backend HMAC/recovery
  model untouched.
- Compatibility: same-named adapters preserve every internal call
  signature; route shapes, auth, and restart behavior unchanged.

## Tests and verification

- New `test_chat_action_forms.py`: 6/6 (node-executed; covers every
  preserved contract in the slice brief: actionable vs locked forms,
  Confirm/Cancel transitions via cancel/side-effect predicates,
  repeated-action prevention inputs, recovered-form state via
  snapshot/terminal helpers, watch interpretation, follow-up inputs
  via watch/run keys, invalid-metadata edges, restart/progress
  presentation, empty/error responses).
- Differential proof (scratch `/tmp/diff_forms.js`, not committed):
  pre-rewire page originals (extracted from a pre-edit backup) vs new
  module over a 113-vector battery (predicates × specs × watch states
  × elapsed forms × bar shapes × restart states) with a fixed clock —
  **113/113 match, zero mismatches**.
- Focused: `test_chat_action_forms` + `test_action_forms` +
  `test_action_form_routes` + `test_action_form_process_restart` +
  `test_chat_page_js_syntax` → **59 passed, 1 skipped**.
- Neighbors (restart, message/history, follow-up, attachments,
  project, slash): 94 passed; 6 failures proven pre-existing by
  re-running them on clean HEAD (`git stash -u`) — identical 6 fail
  without this change (stale `?v` pins, history-gate and attachment
  suites unrelated to this slice).
- Broad: `.venv/bin/python -m pytest -q` → **1,787 passed, 26 failed,
  60 skipped**; the 26 failures byte-identical to the pre-change set
  (verified via `diff` of sorted FAILED lists before/after — no new
  failures; +6 passed = the new tests).
- `node --check` clean on both JS files (via
  `ELECTRON_RUN_AS_NODE=1` electron binary — no system node on PATH;
  a `/tmp/nodeshim/node` shim provided `node` for pytest's
  `shutil.which("node")` gate; shim lives outside the repo).
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip, browser card click-through.

## Metrics

| Metric | Before (`be530dbd`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,704 | 25,591 (−113) | `wc -l` |
| Action-forms decision fns needing page scope | 15 + 3 DOM-coupled + 2 tables | 0 in module (explicit inputs) | grep |
| Action-forms behavior tests | backend-only | +6 module tests, real `require` | pytest |
| Differential old-vs-new | — | 113/113 match | node harness |
| Full suite | 1,781 / 26 / 60 | 1,787 / 26 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Card rendering, run/dismiss/fetch transport, watch poll loops,
   snapshot persistence, sessionStorage IO, history integration, and
   linked-restart event broadcast stay in the page (correct per slice
   scope — they are DOM/IO/orchestration, not decisions).
2. `watch-state` remains authentication-only rather than
   chat-owner-bound (documented non-blocking item from Slice 2; still
   deferred).
3. The restart-suite → auth-suite test-ordering pollution noted in
   Slice 2 still stands; untouched here.
4. No system `node` on this machine's PATH — JS verification depends
   on the vendored electron binary (`ELECTRON_RUN_AS_NODE=1`); CI
   environments with real node are unaffected (tests gate on
   `shutil.which("node")`).
5. 26 baseline failures remain untouched and unrelated (exact same
   set before/after).

## Diff summary

- Added: `src/web/js/chat_action_forms.js` (297 L),
  `src/tests/test_chat_action_forms.py` (6 tests).
- Modified: `src/web/js/chat_page.js` (−113 net: 20 spans rewired
  to adapters + 5 qualified table accesses),
  `src/web/chat_page.html` (+1 script tag, `?v` bump),
  `docs/reviews/architecture-stabilization.md` (this section).
- Deleted: no files.
- Insertions/deletions (`git diff --numstat`): chat_page.js
  +41/−154 (net −113 lines); chat_page.html +2/−1; review doc +234/−0
  (new files untracked: `chat_action_forms.js` 297 L,
  `test_chat_action_forms.py`).
- `git status --short` before commit: 3 modified
  (`src/web/js/chat_page.js`, `src/web/chat_page.html`,
  `docs/reviews/architecture-stabilization.md`) + 2 new
  (`src/web/js/chat_action_forms.js`,
  `src/tests/test_chat_action_forms.py`).

## External Review Summary

1. **What changed architecturally?** Action-forms card/watch/restart
   decision logic moved to owned pure `chat_action_forms.js`; the
   page keeps rendering, transport, timers, storage, history, and
   orchestration behind thin same-signature adapters.
2. **What behavior intentionally changed?** Nothing — differential
   proof 113/113; adapters preserve signatures; const accesses only
   re-qualified.
3. **What behavior should be identical?** Confirm/Cancel/dismiss,
   one-shot locking, already-locked behavior, watch-state
   interpretation, follow-up behavior, restart-recovered and
   persisted-card presentation, session/form identifiers,
   error/toast/loading/progress/disabled states, repeated-click
   prevention.
4. **What remains coupled or messy?** All DOM rendering and IO stays
   in the 25.6k-line page; watch poll orchestration and linked-event
   broadcast remain page-owned; 26 unrelated baseline failures
   remain.
5. **What should be reviewed before the next chat domain?** The
   decision/rendering split (rendering stays by design); whether the
   next slice is composer, streaming, or message rendering; the
   deferred `watch-state` ownership note.
6. **Is the next domain safe to begin?** This slice is self-contained
   (no composer/streaming/messages/attachments/agent-controls logic
   touched; failures identical to baseline). Do NOT continue in this
   track until this review is approved.

---

# Phase 3 — Slice 4: Activity / Unread / Follow-up Queue Domain (chat_page.js)

## Phase status

- Slice: Phase 3 Slice 4 — chat activity, unread/seen, attention,
  chirp eligibility, follow-up queue interpretation, and session-id
  normalization → `src/web/js/chat_activity.js`
  (`window.CuttleChatActivity`).
- Git baseline before work: `0b9a53b9` ("Phase 3 slice 3:
  action-forms frontend domain"), clean tree.
- Git commit after work: the single `Phase 3 slice 4:
  activity/unread/follow-up domain` commit on main (identify via
  `git log --oneline`).
- Completion status: **complete, awaiting external review**. No
  further chat-page domains started (composer, streaming,
  messages/history rendering, attachments, agent/model controls
  untouched except the narrow adapter interface described below).

## Original problem

`chat_page.js` owned the entire activity decision layer — session-id
normalization/equivalence, unread/seen predicates, the error > unread
> queued > paused attention priority, the open-chat attention state
machine, chirp cooldown, the response-ready branch plan, follow-up
queue parsing/normalization/partition/batching, and live-status
activity — as closures interleaved with DOM badges, sounds, toast
rendering, fetch/poll loops, persistence IO, and streaming
orchestration. The behavior could only be tested by slicing source
text out of the 25.6k-line page, and several suites did exactly that
(brittle marker coupling: attention-dots, handle-links,
subagents-ui, project-chip, mobile-webview).

## Before implementation (12-class inventory)

~40 name-matched bindings triaged (25,591-line page):

1. **Unread state** (moved decisions): `sessionHasUnread` (open +
   visible reads as not-unread; watermark fallback),
   `sessionPrefsHasUnreadFlag` / `sessionPrefsUnreadIsError` (prefs
   rows now explicit inputs), `sessionHasUnreadError`,
   `lastAssistantMessageFromSessionObj`.
2. **Activity/running state** (moved): `liveStatusLooksActive`
   (cancelled veto, generating lock, 3-minute staleness gate),
   `activityClassToKind` (new owned mapping for the shell snapshot).
3. **Attention/chirp decisions** (moved): `shouldChirp` (8s
   one-chirp-per-completion cooldown),
   `responseReadyPlan` (new dispatch plan: chirp × viewing ×
   mark × toast), `assistantReplyLooksLikeError`.
4. **Follow-up queue state** (moved pure cores):
   `parseFollowupQueue`, `followupQueueFingerprint`,
   `normalizeFollowupItem` (shared server-take + local normalizer),
   `partitionFollowupForDrain` (batch vs paused/under-edit),
   `queueHasActive` / `queueHasPaused`.
5. **Pending follow-up result interpretation**:
   `collectPendingResult` — stays (streaming fetch orchestration;
   fused with typing UI and nav-generation guards).
6. **Seen/read suppression** (moved transitions):
   `manualHoldBlocksRead`, `releaseManualHold` (re-open counts as
   open-again; leaving releases).
7. **Notification/toast decisions** (moved plan, kept rendering):
   toast branch of `notifyAssistantResponseReady` now follows
   `responseReadyPlan`; `showToast`/title stay in the page.
8. **Session/chat identity normalization** (moved verbatim):
   `toAuthDbSessionId` (CH- refs → digits), `canonicalize-
   ChatSessionId`, `sessionIdsEqual`, `formatChatDisplayId`.
9. **DOM badge/rendering**: title dots, running icon, history
   indicators, icon HTML, jump button, attention listeners — stay.
10. **Polling/fetch transport**: follow-up persist/take/drain,
    pending-result poll, live-status fetch, activity broadcast
    timer, shell postMessage — stay.
11. **Persistence/storage**: session prefs IO, `chatSessions`
    reads, follow-up server patch, terminal registry — stay.
12. **False friends** (verified out of scope): `trySteerRunningTurn`
    (agent steer), supervised cards/jump notifications,
    `mediaCtxToast`, `notifyShellPaneActivity`, terminal sessions,
    `formatPendingStat`/pending-diff modal (Git UI),
    `combineFollowupBatch`'s neighbors `formatMessageWithAttachments`
    (message rendering) and `getStickySlashCommandFromMessage`
    (slash domain, injected as a callback).

Shared bindings the moved logic needed: `currentSessionId`,
`pageIsBackgrounded()`, prefs rows, live `pendingFollowups`,
chirp timestamps, attention flags — all now explicit plan/state
inputs or narrow gather-then-delegate adapters. No moved function
was exposed on `window.*`; no callers exist outside `chat_page.js`
(verified by repo-wide grep). Shell cross-link
(`cuttle-chat-activity` postMessage → Spaces indicators in
`app_shell.js`) is a transport contract and stays in the page;
`app_shell.js` logic was not merged.

## Changes made

- **Added `src/web/js/chat_activity.js`** (535 L): 31 pure
  functions behind `CuttleChatActivity` (classic script + node
  exports; footer uses TDZ-proof `globalThis`). New names with no
  page original (all covered by new tests): `prefsHasUnreadFlag`,
  `prefsUnreadIsError` (prefs-row forms), `queueHasActive` /
  `queueHasPaused`,
  `manualHoldBlocksRead`, `releaseManualHold`, the five
  `attentionAfter*` reducer transitions + `defaultAttentionState` +
  `attentionIsActive`, `shouldChirp`, `responseReadyPlan`,
  `normalizeFollowupItem`, `partitionFollowupForDrain`,
  `activityClassToKind` (31 total). No `document`/`window`/
  `localStorage`/`fetch` in the module.
- **chat_page.js keeps**: DOM badges/rendering, sounds, toast
  rendering, all fetch/poll loops, persistence IO, streaming
  orchestration, message rendering, session switching, history-group
  assembly (now calling extracted per-session decisions via
  adapters), plus thin same-signature adapters (10 pure
  delegations + 12 gather-then-delegate) and two page-owned
  snapshot/apply helpers for the attention flags (state stays in
  the page; transitions are pure).
- `notifyAssistantResponseReady` refactored to execute a
  `responseReadyPlan` — every side-effect statement preserved
  verbatim (chirp stamp/play, mark read/unread, attention note,
  pending-changes refresh, chirp-heal fetch, voice hook, toast,
  history sync/reload); proven equivalent by full-effect
  differential below.
- `drainNextFollowup`: 3 local filter-pair sites now use
  `partitionFollowupForDrain`; 2 normalize maps now use
  `normalizeFollowupItem`. The server-take `: filter(x => x.paused)`
  fallback is intentionally untouched (server owns the queue in
  that branch).
- One deliberate hardening: `normalizeFollowupItem` is null-safe
  where the inline maps threw on a null item (never observed;
  documented, not behavior relied upon).
- chat_page.js: 25,591 → 25,459 lines (−132 net).
- `chat_page.html`: `chat_activity.js` script tag before
  `chat_page.js` (both `?v=20261001slice4` cache-busted).
- **Tests:** new `src/tests/test_chat_activity.py` (7 tests,
  node-executed against the real module): identity normalization,
  unread + attention priority, hold + attention reducer, chirp +
  ready plan, follow-up interpretation, live-status + snapshot
  mapping, parse check. Repaired 5 slicing suites to require the
  module instead of moved bodies (attention-dots priority now
  behavioral; handle-links/subagents-ui/project-chip drivers gain
  one require line; mobile-webview CH- pin repointed at the owner).
- Moved vs deleted: decision logic relocated verbatim (moved);
  original bodies + duplicated normalize/partition inline code
  deleted (rewire asserts: brace balance, `}`/`;` endings,
  single-occurrence targeted replaces).

## Architecture after

```
chat_page.js (badges, sounds, toasts, fetch/poll, storage, streaming,
              history assembly, shell broadcast, session switching)
      │  same-signature adapters / dispatch plan / pure reducer + apply
      ▼
chat_activity.js (identity + unread/seen + attention + chirp +
                  follow-up interpretation + live-status activity)
```

One-way dependency (page → namespace); module holds no state and
reads no page globals. Attention flags and the chirp-timestamp map
stay in the page (mutable runtime state); the module owns the
transitions over explicit snapshots. Follow-up cross-domain needs
(sticky prefix, attachment formatting) inject as callbacks —
the module does not import the slash or message domains.

## Dependencies and state

- Removed: chat-page closures over identity/unread/attention/chirp/
  follow-up/live-status interpretation for all moved decisions.
- Introduced: `CuttleChatActivity` namespace (classic script +
  node exports; no new runtime deps). No shared state added or
  moved; `snapshotChatAttention`/`applyChatAttention` are the only
  new page bindings (plain gather/apply, no logic).
- Reverse deps: none existed outside the page; none created.
- Persistence/restart-sensitive state: unchanged (prefs IO,
  follow-up server sync, live-status cache, pending-result flow
  stay in the page). Backend contracts untouched.
- Compatibility: same-named adapters preserve every internal call
  signature; `combineFollowupBatch(items)` keeps its signature
  with page-injected deps.

## Tests and verification

- New `test_chat_activity.py`: 7/7 (node-executed; covers every
  preserved contract in the slice brief: unread → seen
  transitions, active vs idle, running/completed, chirp
  allowed/suppressed, same/other-session, duplicate suppression,
  enqueue-relevant fingerprint/normalize/partition, pending-result
  inputs via run keys, malformed state, id aliases/equivalence,
  cross-session attention priority).
- Differential proof (scratch `/tmp/diff_activity.js`, not
  committed): pre-rewire page originals (from a pre-edit backup)
  vs new module + rewired adapters over a 128-case battery —
  pure fns, unread chain under stubbed page scope, attention
  reducer + hold (state + call log), `notifyAssistantResponseReady`
  full side-effect log + state under stubbed DOM/IO/fetch, and
  combine/normalize/partition — **128/128 match, zero mismatches**
  (frozen clock; 4 initial diffs were wall-clock artifacts in the
  harness itself).
- Focused + repaired: activity, attention-dots, handle-links,
  subagents-ui helpers, project-chip, action-forms backend,
  page-syntax → green; `test_notify_passes_error_flag` passes
  unmodified (refactor preserved its asserted strings).
- One genuine catch during verification:
  `test_mobile_webview_hardening::test_adopt_chat_session_refuses_numeric_hijack`
  pinned `/^CH-/i.test(s)` inside the moved `toAuthDbSessionId`
  body — repaired to pin the guard in the page plus the parse in
  the owned module (adopt behavior itself unchanged).
- Neighbors (live-status, Spaces activity ×2, cross-session,
  follow-up ×3, busy-zombie, false-reply, pagination, history ×2,
  project, slash, restart, attachments): 109 passed; 8 failures
  all proven pre-existing on clean HEAD (`git stash -u`), including
  the chirp-adjacent `test_cursor_stream_switch_does_not_chirp_mid_run`
  and the live-status backend test.
- Broad: `.venv/bin/python -m pytest -q` → **1,794 passed, 26
  failed, 60 skipped**; the 26 failures byte-identical to the
  pre-change set (verified via `diff` of sorted FAILED lists
  before/after — no new failures; +7 passed = the new tests).
- `node --check` clean on all touched JS (via
  `ELECTRON_RUN_AS_NODE=1` electron binary + `/tmp/nodeshim/node`
  shim for pytest's `shutil.which("node")` gate; shim lives outside
  the repo).
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip, browser dot/chirp/queue
  click-through.

## Metrics

| Metric | Before (`0b9a53b9`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,591 | 25,459 (−132) | `wc -l` |
| Activity decision fns needing page scope | ~31 (all) | 0 in module (explicit inputs) | grep |
| Activity behavior tests | string pins + slices | +7 module tests; 5 files repaired to require | pytest |
| Differential old-vs-new | — | 128/128 match | node harness |
| Full suite | 1,787 / 26 / 60 | 1,794 / 26 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. DOM badges, sounds, toast rendering, fetch/poll loops,
   persistence IO, streaming orchestration, message rendering,
   history-group assembly, shell broadcast, and session switching
   stay in the page (correct per slice scope).
2. `collectPendingResult` remains streaming orchestration with the
   result-mapping fused into its poll loop — a future streaming
   slice (not this one) owns that separation.
3. `normalizeFollowupItem` null-safety is a deliberate micro-
   hardening (inline maps threw on null items); no caller passes
   null today.
4. No system `node` on this machine's PATH — JS verification
   depends on the vendored electron binary (as in Slice 3).
5. 26 baseline failures remain untouched and unrelated (exact same
   set before/after).

## Diff summary

- Added: `src/web/js/chat_activity.js` (535 L),
  `src/tests/test_chat_activity.py` (7 tests).
- Modified: `src/web/js/chat_page.js` (−132 net: 24 spans rewired
  + 6 targeted delegation replaces), `src/web/chat_page.html`
  (+1 script tag, `?v` bump), 5 test files (require lines +
  behavioral priority test + hijack-pin repoint),
  `docs/reviews/architecture-stabilization.md` (this section).
- Deleted: no files.
- Insertions/deletions (`git diff --numstat`): chat_page.js
  +129/−261; chat_page.html +2/−1; test repairs +63/−18 total
  (new files untracked: `chat_activity.js` 535 L,
  `test_chat_activity.py` ~270 L).
- `git status --short` before commit: 7 modified
  (`src/web/js/chat_page.js`, `src/web/chat_page.html`, 5 test
  files) + 2 new (`src/web/js/chat_activity.js`,
  `src/tests/test_chat_activity.py`).

## External Review Summary

1. **What changed architecturally?** Activity, unread/seen,
   attention, chirp, follow-up interpretation, live-status
   activity, and session-id normalization moved to owned pure
   `chat_activity.js`; the page keeps badges, sounds, toasts,
   fetch/poll, storage, streaming, rendering, history assembly,
   broadcast, and session switching behind thin adapters and a
   response-ready dispatch plan.
2. **What behavior intentionally changed?** Nothing except
   null-safety in `normalizeFollowupItem` (dead path today) —
   differential 128/128; adapters preserve signatures; notify
   side effects preserved verbatim.
3. **What behavior should be identical?** Unread indicators,
   active/running indicators, chirp timing, notification
   suppression, seen/read semantics, follow-up ordering and
   batching, pending behavior, session switching, new/existing
   chat behavior, live-status interaction, Spaces activity
   indicators, toast/sound timing, session-id equivalence.
4. **What remains coupled or messy?** All rendering/IO/polling in
   the 25.5k-line page; `collectPendingResult` still fuses poll
   loop with result mapping; server-take remaining-branch keeps
   its own filter; 26 unrelated baseline failures remain.
5. **What should be reviewed before the next chat domain?** The
   reducer + dispatch-plan shape (state stays, transitions move)
   as the pattern for composer/streaming; whether the next slice
   is composer, streaming, or message rendering; the fused
   pending-result loop as streaming-slice input.
6. **Is the next domain safe to begin?** This slice is
   self-contained (no streaming implementation, message
   rendering, composer/send, attachments, agent controls, Action
   Forms, or slash logic touched; failures identical to
   baseline). Do NOT continue in this track until this review is
   approved.

---

# Phase 3 — Slice 5: Attachments Domain (chat_page.js)

## Phase status

- Slice: Phase 3 Slice 5 — attachment normalization, classification,
  pending-list transitions, upload planning, message/request mapping,
  and history-note parsing → `src/web/js/chat_attachments.js`
  (`window.CuttleChatAttachments`).
- Git baseline before work: `dd09fd6d` ("Phase 3 slice 4:
  activity/unread/follow-up domain") plus `5d72e8ad` (venv-rebuild
  `.gitignore` housekeeping; no product code), clean tree.
- Environment note: suite runs on the rebuilt Python 3.14 venv
  (see gray-screen recovery); baseline below is on that env.
- Git commit after work: the single `Phase 3 slice 5: attachments
  domain` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No
  further chat-page domains started (Composer send flow, generic
  Message Rendering, Streaming, Agent/Model controls untouched
  except the narrow adapter interface described below).

## Original problem

`chat_page.js` owned the entire attachments decision layer —
image/file classification, item normalization, pending-list
append/remove, upload-result interpretation, request-payload and
message-HTML branch mapping, outbound note formatting, and legacy
history-note inference — as closures interleaved with chip DOM,
upload fetch, persistence IO, message rendering, and send
orchestration. There were no frontend behavior tests at all for
this domain (the existing `test_chat_attachments.py` covers only
the backend vision pre-pass, and 3 of its tests fail at baseline).

## Before implementation (13-class inventory)

~20 name-matched bindings triaged (25,459-line page):

1. **Pending attachment state** (`pendingAttachments` let,
   `_pendingKey`, save/restore/clear, `take/clearPending-
   Attachments`) — stays (mutable runtime state + storage IO).
2. **Attachment normalization** (moved): `normalizeAttachmentList`
   (canonical `{filename, path, mime, size, url}` shape).
3. **File/media type classification** (moved): `isImageAttachment`
   (mime, then png/jpg/gif/webp/bmp extension; PDFs never thumbs).
4. **Upload/staging metadata** (moved plan): `uploadResultPlan`
   (server-error precedence, HTTP fallback message); FormData/
   fetch/session-id stay in the page.
5. **Message attachment mapping** (moved decision, kept
   assembly): `messageAttachmentKind` (thumb vs link vs span);
   `buildMessageAttachmentsHtml` stays (escaping + media download
   URLs live in the message/media domains).
6. **Request payload construction** (moved):
   `requestAttachmentPayload` (`{filename, path, mime, url}` — no
   `size`; server resolves content from path/url).
7. **Display/thumbnail metadata decisions** (moved kind only):
   chip classes/thumbnails stay (composer DOM rendering).
8. **Attachment removal/reordering** (moved transitions):
   `removeAttachmentAt` (chip ✕ by index, out-of-range safe),
   `addAttachmentsToPending` (order-preserving append, no dedupe —
   documents historical push behavior). No reorder UI exists.
9. **Drag/drop/picker interpretation**: no drag/drop or paste
   handlers exist — uploads enter only via the picker
   (`wireAttachControls`, stays). Nothing to extract; documented.
10. **DOM rendering** (`renderAttachmentChips`,
    `buildMessageAttachmentsHtml`, chip builders) — stay.
11. **Upload/fetch transport** (`uploadChatFiles` fetch flow) —
    stays as orchestrator, now executing the plan.
12. **Persistence/history interaction** (pending localStorage,
    `formatUserMessageForDisplay` note flow, send-path `format` /
    `take` calls) — stay, calling via adapters.
13. **False friends** (verified out of scope):
    `attachAgentIdentityToRequest` (agent pins),
    `attachCodeCopyButtons` (code rendering), `mediaDownloadUrl` /
    `mediaDownloadFilename` (media/lightbox domain),
    `pendingDiff*` (Git UI), `trySteerRunningTurn` (agent steer).

Shared bindings the moved logic needed: `currentSessionId`
(infer folders), `pendingAttachments` (transitions return new
arrays; the page reassigns + persists), fetch results — all
explicit inputs or narrow adapters. No moved function was exposed
on `window.*`; no callers exist outside `chat_page.js` (verified
by repo-wide grep), and no existing test slices these functions
(verified — zero test references). Backend contract verified
read-only: `POST /api/upload` → `{success, files: [{filename,
path, mime, size, url}]}` (png/jpg/jpeg/gif/webp/bmp/pdf, 25MB).

## Changes made

- **Added `src/web/js/chat_attachments.js`** (174 L): 11 pure
  functions behind `CuttleChatAttachments` (classic script + node
  exports; footer uses TDZ-proof `globalThis`). New names with no
  page original (all covered by new tests):
  `requestAttachmentPayload`, `addAttachmentsToPending`,
  `removeAttachmentAt`, `messageAttachmentKind`,
  `uploadResultPlan`. No `document`/`window`/`localStorage`/
  `fetch` in the module.
- **chat_page.js keeps**: pending state + storage key + persistence
  IO, chip rendering, picker wiring, upload fetch flow,
  message-HTML assembly, history/persist/send orchestration, plus
  thin same-signature adapters (6 pure delegations + 1
  gather-then-delegate for session folders).
- `uploadChatFiles` refactored to execute `uploadResultPlan` —
  every transport/state side effect preserved verbatim (FormData,
  session id, fetch, toast, push, save, render); proven equivalent
  by full-effect differential below.
- Send flow: request-payload map now calls the owned mapping
  (output byte-identical). Message HTML: thumb/link branch
  conditions now call the owned kind (rendered HTML proven
  identical). Chip ✕: splice now calls the owned transition.
- chat_page.js: 25,459 → 25,422 lines (−37 net).
- `chat_page.html`: `chat_attachments.js` script tag before
  `chat_page.js` (page `?v=20261001slice5` cache-busted; domain
  tags keep their slice versions).
- **Tests:** new `src/tests/test_chat_attachments_frontend.py`
  (8 tests, node-executed against the real module — the domain's
  first frontend tests): classification, normalization,
  note strip/parse, legacy inference, outbound formatting,
  pending/payload/kind mapping, upload plans, parse check. No
  existing-test repairs needed (nothing sliced these functions).
- Moved vs deleted: decision logic relocated verbatim (moved);
  original bodies + inline request-map/push/splice code deleted
  (rewire asserts: brace balance, `}`/`;` endings,
  single-occurrence targeted replaces).

## Architecture after

```
chat_page.js (pending state, persistence, chips, picker, upload fetch,
              message-HTML assembly, history/send orchestration)
      │  same-signature adapters / upload plan / kind + payload mapping
      ▼
chat_attachments.js (classify + normalize + infer + format + plan +
                     pending transitions + request/display mapping)
```

One-way dependency (page → namespace); module holds no state and
reads no page globals. Pending-list mutations return new arrays;
the page reassigns, persists, and re-renders. Cross-domain needs
(escaping, media download URLs, sticky context) stay in the page.

## Dependencies and state

- Removed: chat-page closures over classification/normalization/
  inference/formatting/upload-interpretation/mapping for all moved
  decisions.
- Introduced: `CuttleChatAttachments` namespace (classic script +
  node exports; no new runtime deps). No shared state added or
  moved.
- Reverse deps: none existed outside the page; none created.
- Persistence/upload-sensitive state: unchanged (pending
  localStorage, session folders, `/api/upload` flow stay in the
  page). Backend vision/upload contracts untouched.
- Compatibility: same-named adapters preserve every internal call
  signature; Slice 4's `combineFollowupBatch` adapter keeps
  receiving the page's `formatMessageWithAttachments` reference
  (now an adapter — resolution unchanged).

## Tests and verification

- New `test_chat_attachments_frontend.py`: 8/8 (node-executed;
  covers the brief: normalization, duplicate inputs kept in
  order, add/remove transitions incl. out-of-range, malformed
  metadata, image vs non-image, staged/uploaded mapping, request
  payload mapping, stored-message note mapping, empty state,
  filename/path edges).
- Differential proof (scratch `/tmp/diff_attachments.js`, not
  committed): pre-rewire page originals (from a pre-edit backup)
  vs new module + rewired adapters over a 47-case battery — pure
  fns, session-scoped inference, rendered message HTML under
  stubbed escaping (thumb/link/span/evil-input/empty), upload
  flow full side-effect log + state (ok/server-error/HTTP-500),
  chip removal, request payload — **47/47 match, zero
  mismatches**.
- Focused: frontend (8 new) + backend attachments + page-syntax
  + activity/project/slash → 41 passed; the only 3 failures are
  the known backend baseline failures (proven pre-existing in
  Slices 3–4).
- Neighbors (message pagination, history search, follow-up ×2,
  stop-then-followup, action forms, restart): 89 passed; 2
  failures both known pre-existing (stale `?v` pins, unchanged by
  the slice5 bump which keeps them failing identically).
- Broad: `.venv/bin/python -m pytest -q` → **1,802 passed, 26
  failed, 60 skipped**; the 26 failures byte-identical to the
  pre-change set (verified via `diff` of sorted FAILED lists —
  no new failures; +8 passed = the new tests).
- `node --check` clean on all touched JS (vendored electron
  binary + `/tmp/nodeshim/node` shim as in prior slices).
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip, browser picker/upload/chip
  click-through.

## Metrics

| Metric | Before (`dd09fd6d`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,459 | 25,422 (−37) | `wc -l` |
| Attachment decision fns needing page scope | ~11 (all) | 0 in module (explicit inputs) | grep |
| Attachment frontend behavior tests | 0 (backend only) | +8 module tests, real `require` | pytest |
| Differential old-vs-new | — | 47/47 match | node harness |
| Full suite | 1,794 / 26 / 60 | 1,802 / 26 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Pending state, storage key/format, chip rendering, picker
   wiring, upload fetch, message-HTML assembly, history/send
   orchestration stay in the page (correct per slice scope).
2. No drag/drop or paste upload path exists (picker-only) — a
   future composer enhancement, not stabilization work.
3. `uploadResultPlan` items flow through `addAttachmentsToPending`
   (re-normalization is idempotent; proven by differential).
4. Backend `test_chat_attachments.py` 3 failures remain untouched
   (documented baseline; backend vision behavior out of scope).
5. No system `node` on this machine's PATH (vendored electron
   binary used, as in prior slices).

## Diff summary

- Added: `src/web/js/chat_attachments.js` (174 L),
  `src/tests/test_chat_attachments_frontend.py` (8 tests).
- Modified: `src/web/js/chat_page.js` (−37 net: 7 spans rewired
  + 4 targeted delegation replaces), `src/web/chat_page.html`
  (+1 script tag, page `?v` bump),
  `docs/reviews/architecture-stabilization.md` (this section).
- Deleted: no files. No existing-test repairs needed.
- Insertions/deletions (`git diff --numstat`): chat_page.js
  +28/−65; chat_page.html +2/−1 (new files untracked:
  `chat_attachments.js` 174 L,
  `test_chat_attachments_frontend.py` ~200 L).
- `git status --short` before commit: 2 modified
  (`src/web/js/chat_page.js`, `src/web/chat_page.html`) + 2 new
  (`src/web/js/chat_attachments.js`,
  `src/tests/test_chat_attachments_frontend.py`).

## External Review Summary

1. **What changed architecturally?** Attachment classification,
   normalization, pending transitions, upload planning, and
   message/request mapping moved to owned pure
   `chat_attachments.js`; the page keeps pending state,
   persistence, chips, picker, upload fetch, HTML assembly, and
   send/history orchestration behind thin adapters and an upload
   plan.
2. **What behavior intentionally changed?** Nothing —
   differential 47/47 (including rendered-HTML equivalence);
   adapters preserve signatures; upload/send/payload side
   effects preserved verbatim.
3. **What behavior should be identical?** Picker behavior, multiple
   attachments, upload/staging, removal, request metadata, message
   metadata, thumbnail/link/fallback rendering, image/non-image
   classification, names/labels, ordering (incl. duplicate
   staging), draft/pending behavior, new/existing chats, payload
   format, history re-render.
4. **What remains coupled or messy?** All rendering/IO/fetch in
   the 25.4k-line page; chip rendering unowned by the module by
   design; no drag/drop path exists; 26 unrelated baseline
   failures remain (incl. 3 backend attachment tests).
5. **What should be reviewed before the next chat domain?** The
   plan + kind-decision shape for composer-adjacent work; whether
   the next slice is Composer, Messages/History, Streaming, or
   Agent/Model controls; the picker-only upload path as composer
   input.
6. **Is the next domain safe to begin?** This slice is
   self-contained (no Composer send flow, generic message
   rendering, Streaming, Agent/Model, Slash, Action Forms, or
   Activity logic touched; failures identical to baseline). Do
   NOT continue in this track until this review is approved.

---

# Phase 3 — Slice 6: Composer Domain (chat_page.js)

## Phase status

- Slice: Phase 3 Slice 6 — send eligibility, keyboard-submit
  interpretation, sticky resolution after send, control-lane text
  classification, and the send-dispatch plan →
  `src/web/js/chat_composer.js` (`window.CuttleChatComposer`).
- Git baseline before work: `38484204` ("Phase 3 slice 5:
  attachments domain"), clean tree.
- Git commit after work: the single `Phase 3 slice 6: composer
  domain` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. No
  further chat-page domains started (Streaming, generic
  Messages/History, Agent/Model controls untouched except the
  narrow adapter interface described below).

## Original problem

`chat_page.js` owned the entire composer decision layer — what
counts as sendable, what Enter means, which sticky chip survives a
send, what bypasses the follow-up queue, and the guard/duplicate/
follow-up/normal dispatch through `sendMessage` — as closures
interleaved with textarea DOM, draft persistence, fetch/SSE
initiation, streaming lifecycle, and history persistence. The
branchiest logic (`isSendableComposerMessage`'s strip loop,
`sendMessage`'s dispatch) could only be tested by slicing source
text, and several suites did exactly that (bare-sticky,
palette-consistency, cost/usage one-shot pins).

## Before implementation (17-class inventory)

~25 name-matched bindings triaged (25,422-line page):

1. **Composer text/input state** (draft save/restore/clear/
   migrate, `_readComposerDraftText`) — stays (persistence IO).
2. **Send eligibility** (moved): `isSendableComposerMessage`
   (attachments win; sticky-only tokens not prompts; control +
   nested one-shots sendable; lone `/model` not).
3. **Send-mode decisions** (moved plan): `composerSendPlan`
   (ignore-guard / ignore-empty / ignore-duplicate / followup /
   normal + outbound + controlLane).
4. **Enter/Shift+Enter/control-key interpretation** (moved):
   `enterSubmits` (Shift never submits; touch needs Ctrl/Meta).
5. **Empty-input handling**: inside sendability (moved) +
   send-path early return (stays, calls via adapter).
6. **Attachment-aware send decisions**: sendability head +
   outbound fallback (moved); take/clear/queue (stay, ordered by
   plan).
7. **Slash/sticky interaction** (moved resolution, kept
   application): `stickyCommandAfterSend`; `composeMessage-
   WithSlashChip`, chip render/persist stay. Slash parsing itself
   stays in `CuttleChatSlash`, injected as callbacks.
8. **Project-context stamping**: `applyOutboundProjectToRequest`
   (project domain) — called, not moved.
9. **Agent/model request inputs**: `ensureCodexEffortForSend`,
   `maybeRefreshCursorModelsCatalogFromOutbound` — called, not
   moved (Agent/Model slice input).
10. **Follow-up vs normal-turn decisions** (moved plan,
    executed by page): steer-or-queue stays (agent controls).
11. **Running-turn steer/queue decisions**: `trySteerRunningTurn`
    stays; the plan only selects the follow-up action.
12. **Request payload planning**: `requestBody.attachments` map
    owned by Slice 5; control-lane flags stay in send flow.
13. **Input clearing/restoration**: `clearComposer` closure, draft
    fns stay (DOM + persistence).
14. **DOM/textarea rendering** (resize, focus, hints, emoticons,
    keydown wiring, stop-button sync) — stay.
15. **Fetch/stream initiation** (`processMessage`,
    `beginLocalGeneration`, guard claim/release) — stay.
16. **Persistence/history side effects** (save, prompt history,
    pending clear, card dismiss) — stay, ordered by plan.
17. **False friends** (verified out of scope):
    `enhanceComposerPrompt` (Enhance feature),
    `sendVoiceUtterance`/voice-silence fns (voice modality —
    duplicates the steer/queue decision inline via adapters),
    `stopGenerating`, `sendWelcomeMessage`'s welcome→chat handoff
    screens (stays; delegates to `sendMessage`),
    `collectPendingResult` (Streaming slice input, per Slice 4).

Shared bindings the moved logic needed: `slashCtx` chips (sticky
application stays; resolution moves), `sendDispatchGuard`,
`isSessionGenerating()` (read twice — pre/post-heal; both reads
preserved as plan inputs), `inFlightUserMessage` + queued
contents (page normalizes via the message domain, plan compares
strings), composer text/attachments. No moved function was
exposed on `window.*` (`window.sendMessage` etc. expose the
orchestrators, which stay). No callers exist outside `chat_page.js`
except `landing_page.html`'s `sendMessage` override (different
surface, untouched — verified by grep).

## Changes made

- **Added `src/web/js/chat_composer.js`** (179 L): 5 pure
  functions behind `CuttleChatComposer` (classic script + node
  exports; footer uses TDZ-proof `globalThis`). New names with no
  page original (all covered by new tests): `enterSubmits`,
  `stickyCommandAfterSend`, `composerSendPlan`. No `document`/
  `window`/`localStorage`/`fetch` in the module.
- **chat_page.js keeps**: textarea DOM, focus/caret, event wiring,
  draft persistence, fetch/SSE, streaming lifecycle, history
  persistence, message rendering, guard flag, follow-up
  steer/queue execution, control-lane supervised delegation,
  plus thin same-signature adapters (2 pure delegations + 2
  gather-then-delegate with injected slash callbacks).
- `applyStickySlashAfterComposerSend` refactored to resolve via
  `stickyCommandAfterSend` — control branch, chip application,
  menu/render/persist calls, and `stickyCmd` return preserved
  verbatim.
- `sendMessage` refactored to execute `composerSendPlan` — guard
  read (pre-heal), input read, sendability gate, heal, branch,
  control-bypass log, effort fetch, area swap, paint/save/history,
  clear/chips/resize, generation claim, `processMessage`, guard
  release all preserved in order; proven equivalent by
  full-effect differential below. `ensureCodexEffortForSend`
  still precedes `addMessageToUI` in both send paths (contract
  pinned by `test_codex_starred_effort`).
- chat_page.js: 25,422 → 25,396 lines (−26 net).
- `chat_page.html`: `chat_composer.js` script tag before
  `chat_page.js` (page `?v=20261001slice6` cache-busted; domain
  tags keep their slice versions).
- **Tests:** new `src/tests/test_chat_composer.py` (5 tests,
  node-executed against the real module): enter matrix,
  sendability matrix (bare tokens, control, one-shots, model
  chips, attachments, missing deps), control-lane text + sticky
  resolution, full dispatch-plan matrix incl. heal semantics.
  Repaired 4 slicing suites (bare-sticky + palette harnesses gain
  one require line; cost/usage one-shot pins repointed at the
  owned module).
- Moved vs deleted: decision logic relocated verbatim (moved);
  original bodies + inline strip/branch code deleted (rewire
  asserts: brace balance, `}`/`;` endings, single-occurrence
  spans).

## Architecture after

```
chat_page.js (textarea DOM, drafts, events, fetch/SSE, streaming,
              history, guard flag, steer/queue, paint/save)
      │  same-signature adapters / dispatch plan / sticky resolution
      ▼
chat_composer.js (sendable + enter + sticky-after-send + lane text +
                  send plan) ──inject──▶ CuttleChatSlash (stickyOf,
                  isControl — never duplicated)
```

One-way dependency (page → namespace); module holds no state and
reads no page globals. Slash parsing, attachment notes, and
follow-up interpretation stay in their domains; the composer
consumes them via callbacks and pre-resolved inputs. The guard
flag and generating state stay in the page; the plan reads both
heal snapshots.

## Dependencies and state

- Removed: chat-page closures over sendability, enter meaning,
  sticky-after-send resolution, lane-text fallback, and dispatch
  branching.
- Introduced: `CuttleChatComposer` namespace (classic script +
  node exports; no new runtime deps). No shared state added or
  moved.
- Reverse deps: none created (landing page's own `sendMessage`
  override is a separate surface, untouched).
- Persistence/streaming/agent state: unchanged (drafts, guard,
  generation claim, effort fetch, `processMessage` stay).
  Backend `/api/chat` behavior untouched.
- Compatibility: same-named adapters preserve every internal call
  signature; `window.sendMessage` / `window.sendWelcomeMessage` /
  `window.handleInputKeyDown` exposures unchanged.

## Tests and verification

- New `test_chat_composer.py`: 5/5 (node-executed; covers the
  brief: plain/whitespace/attachment-only/text+attachment sends,
  Enter vs Shift+Enter, touch rules, disabled/busy via guard +
  generating inputs, duplicate suppression, normal vs follow-up,
  steer/queue selection input, slash/sticky inputs, control
  inputs, clear/retain is structural in the page and covered by
  differential, malformed state).
- Differential proof (scratch `/tmp/diff_composer.js`, not
  committed): pre-rewire page originals (from a pre-edit backup)
  vs new module + rewired adapters over a 39-case battery — pure
  fns, sticky application full effect (chips/prefs/return), and
  `sendMessage` full side-effect log + guard/pending/in-flight/
  input state across 12 scenarios (normal, guard idle/generating,
  empty, bare chip, attachment-only, follow-up, duplicate
  in-flight/queued, control-while-generating, missing input,
  steer) — **39/39 match, zero mismatches**. The harness caught
  one genuine regression mid-slice (see below); after the fix,
  clean.
- Genuine catch during verification: the first refactor hoisted
  `takePendingAttachments` + `dismissOpenInteractiveCards` above
  the duplicate check, so ignored duplicates would have swallowed
  staged attachments and dismissed cards. Fixed to preserve the
  original order (duplicate returns before take/dismiss);
  differential confirms. Also normalized `enterSubmits`
  `undefined` → `false` (all 3 call sites use truthiness —
  verified by grep).
- Focused + repaired: composer (5 new), bare-sticky, palette
  consistency, cost, usage, codex-effort, restart composer +
  native, page-syntax → **188 passed**.
- Neighbors (project, slash, attachments ×2, activity,
  attention, follow-up heal, stop-then-followup, pagination,
  history search, action forms, supervised ×2, restart): 178
  passed; 7 failures all proven pre-existing on clean HEAD
  (`git stash -u`).
- Broad: `.venv/bin/python -m pytest -q` → **1,807 passed, 26
  failed, 60 skipped**; the 26 failures byte-identical to the
  pre-change set (verified via `diff` of sorted FAILED lists —
  no new failures; +5 passed = the new tests).
- `node --check` clean on all touched JS (vendored electron
  binary + `/tmp/nodeshim/node` shim as in prior slices).
- Manual workflows: none applicable (no UI changed). Not exercised:
  live Flask boot, chat round-trip, browser Enter/send/chip
  click-through.

## Metrics

| Metric | Before (`38484204`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,422 | 25,396 (−26) | `wc -l` |
| Composer decision fns needing page scope | ~5 (all) | 0 in module (explicit inputs) | grep |
| Composer behavior tests | string pins + slices | +5 module tests; 4 files repaired to require | pytest |
| Differential old-vs-new | — | 39/39 match | node harness |
| Full suite | 1,802 / 26 / 60 | 1,807 / 26 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Textarea DOM, focus/caret, event wiring, draft persistence,
   fetch/SSE, streaming lifecycle, history persistence, message
   rendering, guard flag, steer/queue execution stay in the page
   (correct per slice scope).
2. `sendWelcomeMessage`'s welcome→chat handoff and voice's inline
   steer/queue duplicate stay as orchestration (they delegate to
   the same adapters/plan inputs where they overlap).
3. `collectPendingResult` remains Streaming-slice input (per
   Slice 4); send-path normalization stays in the message domain.
4. No system `node` on this machine's PATH (vendored electron
   binary used, as in prior slices).
5. 26 baseline failures remain untouched and unrelated (exact same
   set before/after).

## Diff summary

- Added: `src/web/js/chat_composer.js` (179 L),
  `src/tests/test_chat_composer.py` (5 tests).
- Modified: `src/web/js/chat_page.js` (−26 net: 5 spans rewired),
  `src/web/chat_page.html` (+1 script tag, page `?v` bump),
  4 test files (require lines + pin repoints),
  `docs/reviews/architecture-stabilization.md` (this section).
- Deleted: no files.
- Insertions/deletions (`git diff --numstat`): chat_page.js
  +49/−75; chat_page.html +2/−1; test repairs +14/−8 total
  (new files untracked: `chat_composer.js` 179 L,
  `test_chat_composer.py` ~200 L).
- `git status --short` before commit: 6 modified
  (`src/web/js/chat_page.js`, `src/web/chat_page.html`, 4 test
  files) + 2 new (`src/web/js/chat_composer.js`,
  `src/tests/test_chat_composer.py`).

## External Review Summary

1. **What changed architecturally?** Send eligibility,
   keyboard-submit meaning, sticky-after-send resolution,
   control-lane text classification, and send dispatch moved to
   owned pure `chat_composer.js`; the page keeps textarea DOM,
   drafts, events, fetch/SSE, streaming, history, guard state,
   and steer/queue execution behind thin adapters and a dispatch
   plan.
2. **What behavior intentionally changed?** Nothing except
   `enterSubmits` normalizing `undefined` → `false` (all call
   sites use truthiness — verified). Differential 39/39;
   adapters preserve signatures; send side effects preserved in
   order.
3. **What behavior should be identical?** Enter/Shift+Enter,
   send-button flow, empty composer, attachment-only and
   text+attachment sends, slash/sticky behavior, `/project`
   interactions, project stamping, follow-up queueing,
   running-turn steer/queue selection, input clearing, enable
   rules, duplicate suppression, new/existing chats, payload
   semantics, agent/model/effort selection.
4. **What remains coupled or messy?** All DOM/IO/fetch/streaming
   in the 25.4k-line page; welcome handoff and voice inline
   duplicates stay as orchestration; 26 unrelated baseline
   failures remain.
5. **What should be reviewed before the next chat domain?** The
   plan + injected-callback shape as the pattern for remaining
   slices; whether the next slice is Agent/Model controls,
   Messages/History, or Streaming; the duplicate-guard/take
   ordering as a pinned subtlety for send-flow refactors.
6. **Is the next domain safe to begin?** This slice is
   self-contained (no Streaming implementation, generic message
   rendering, Agent/Model controls, attachments UX, or palette
   refactors; no backend changes; failures identical to
   baseline). Do NOT continue in this track until this review is
   approved.

---

# Phase 3 — Slice 6 approval (recorded at the Slice 7 append boundary)

- Slice 6 (`be2658e9`, composer domain) is **approved** per the worker
  brief for Slice 7; prior sections above are preserved verbatim —
  reviewer last read line 3201, and these notes sit at the append
  boundary rather than rewriting them.
- Slice 6 review follow-up honored in this slice: the duplicate-guard
  ordering subtlety (ignored duplicate must return before
  `takePendingAttachments` / `dismissOpenInteractiveCards`) is now
  pinned by a durable committed regression test, not just the Slice 6
  scratch differential. New `test_chat_composer.py` coverage:
  `test_ignore_duplicate_plan_contract` (node-executed plan behavior),
  `test_ignore_duplicate_preserves_staged_attachments_and_cards`
  (sendMessage duplicate-branch orchestration effects: no take, clear,
  dismiss, steer, queue, or send effect; only clear + sticky
  re-resolve + resize + return), and
  `test_followup_branch_still_takes_attachments_and_cards`
  (discrimination check that the follow-up branch owns take/dismiss).
  Verified 8/8 with node on PATH.

---

# Phase 3 — Slice 7: Agent/Model Controls (chat_page.js)

## Phase status

- Slice: Phase 3 Slice 7 — agent/model/effort selection decisions →
  `src/web/js/chat_agent_model.js` (`window.CuttleChatAgentModel`).
- Git baseline before work: `be2658e9` ("Phase 3 slice 6: composer
  domain"); tree clean except pre-existing untracked `work/`
  (scratch, left in place, never touched).
- Git commit after work: the single `Phase 3 slice 7: agent/model
  controls domain` commit on main (identify via `git log --oneline`).
- Completion status: **complete, awaiting external review**. Slice 8
  (Messages/History or Streaming) NOT started.

## Original problem

`chat_page.js` owned every Agent/Model control decision inline as
closures over `slashPaletteSupplement`: which backend pin wins
(nested `agent_pins` vs legacy flat keys), starred model/effort
default lookups, starred-default row matching, send-time `agent_pins`
construction (active-harness always-attach vs dirty-only), the Codex
pre-send effort fetch gate, and badge effort resolution — interleaved
with palette/badge DOM, fetch/POST transport, session-prefs IO, and
send orchestration. The decisions could only be tested by slicing
page source (first-turn pins, codex-effort ordering pins).

## Before implementation (inventory)

Agent/Model responsibilities triaged across the 25,396-line page:

1. **Supplement state** (`slashPaletteSupplement`: per-agent
   model/effort/dirty/loading/key + cursor/muse/hermes/opencode/codex
   catalogs + starred maps) — stays (domain state, page-owned).
2. **Pin reads** (moved): `sessionPin` (nested-wins, blank-falls-back).
3. **Starred defaults** (moved lookups, kept IO): `starredAgentModel` /
   `starredAgentEffort` (case-insensitive map read);
   `isAgentDefaultStarred` (row match); loaders/POSTs stay.
4. **Outbound request inputs** (moved build, kept application):
   `attachAgentIdentityToRequest` core → `buildAgentPinsForRequest`;
   `sticky_agent` override stays (already delegates to
   `CuttleChatSlash.stickyAgentOverrideForRequest`); send-path attach
   order (sticky then pins) unchanged.
5. **Pre-send fetch gate** (moved gate, kept IO):
   `ensureCodexEffortForSend` → `codexEffortFetchForSend`; dirty
   re-check after await, assignment, and both send paths'
   ensure-before-`addMessageToUI` ordering preserved.
6. **Badge effort resolution** (moved): fallback chain inside
   `enrichSlashCommandWithAgentBadge`; badge DOM construction stays.
7. **Session restoration** (`restoreSessionStickySlash`,
   `markStickyAgentCleared`, prefs IO) — stays untouched: two suites
   (`test_starred_agent_removal.py`, `test_working_bubble_badge...`)
   execute the real function under node with stubbed prefs, so moving
   it would break their harnesses for no slice benefit. Preserved and
   covered, not absorbed.
8. **Palette DOM, badge paint, catalog fetch/POST, history chips,
   typing-meta, execution orchestration** — stay in their owners.
9. **Explicitly NOT absorbed**: Messages/History, Streaming,
   backend routing, feature redesign. No backend changes.

Shared state the moved logic needed: supplement maps/values (passed
as an explicit `models` snapshot / `pinnedEfforts` array — never the
live object), chip presence + prefs + session key (passed as
booleans/strings into the fetch gate). No moved function touches
`document` / `window` / `localStorage` / `fetch`.

## Changes made

- **Added `src/web/js/chat_agent_model.js`** (169 L): 7 pure
  functions behind `CuttleChatAgentModel` (classic script + node
  exports; footer uses TDZ-proof `globalThis`, same pattern as
  `chat_composer.js`): `sessionPin`, `starredAgentModel`,
  `starredAgentEffort`, `isAgentDefaultStarred`,
  `buildAgentPinsForRequest`, `codexEffortFetchForSend`,
  `resolveAgentEffortForBadge`.
- **chat_page.js keeps**: supplement state, all fetch/POST, palette
  and badge DOM, prefs IO, sticky restore/override orchestration,
  send-path ordering, plus thin same-signature adapters (pure
  delegations + gather-then-delegate for pins/effort/gate).
- `attachAgentIdentityToRequest(requestBody)` keeps its signature,
  guard, and `agent_pins` application; only the build moves.
- `ensureCodexEffortForSend` keeps its async shape, early dirty
  return (still ahead of `await fetch`, so the existing
  dirty-respect pin holds verbatim), in-flight dirty re-check, and
  assignment.
- `enrichSlashCommandWithAgentBadge` keeps agent detection, model
  read, per-harness enrichers, and drift-warning chip; only the
  effort `||` chain moves (end-only trim semantics preserved —
  locked by a whitespace-data edge case in tests).
- chat_page.js: 25,396 → 25,386 lines (−10 net).
- `chat_page.html`: `chat_agent_model.js` script tag before
  `chat_page.js` (page `?v=20261001slice7` cache-busted; domain tags
  keep their slice versions).
- **Tests:** new `src/tests/test_chat_agent_model.py` (7 tests,
  node-executed against the real module + one adapter-delegation
  structural test). Repointed 1 slicing suite
  (`test_first_turn_agent_pins.py` active-identity test now asserts
  module ownership + adapter delegation + send-path call site).
- Moved vs deleted: decision logic relocated verbatim (moved);
  original bodies replaced by adapters (rewire asserts: single
  occurrence spans, brace balance).

## Architecture after

```
chat_page.js (supplement state, palette/badge DOM, fetch/POST,
              prefs IO, sticky restore/override, send ordering,
              badge construction)
      │  same-signature adapters / gather-then-delegate
      ▼
chat_agent_model.js (pin read + starred lookups/row match +
                     pins build + codex fetch gate +
                     badge effort) ──reads──▶ explicit inputs only
                     (models snapshot, pinnedEfforts, keys/flags)
CuttleChatSlash (chip classification — never duplicated here)
```

One-way dependency (page → namespace); module holds no state and
reads no page globals.

## Dependencies and state

- Removed: page closures over pin reads, starred lookups/matching,
  pins construction, fetch gating, badge-effort fallback.
- Introduced: `CuttleChatAgentModel` namespace (no new runtime
  deps). No shared state added or moved; the `models` snapshot is a
  fresh gather per send, never a live reference.
- Reverse deps: none created.
- Persistence/streaming/backend behavior: unchanged. Backend
  `/api/chat` untouched.
- Compatibility: every internal call signature preserved
  (`sessionPin/3`, `starredAgent*(1)`, `isAgentDefaultStarred/2`,
  `attachAgentIdentityToRequest/1`, `ensureCodexEffortForSend/1`).

## Tests and verification

- New `test_chat_agent_model.py`: 7/7 (node-executed; covers pin
  nested/blank-flat/missing/null, starred case-insensitivity,
  row match/mismatch/no-star, pins active/dirty-only/empty/
  effort-only, gate fetch/key-match/dirty/no-codex/chip/no-session
  + URL shape, badge data/generic/pinned/none/null, module parses,
  page-delegation structure).
- Slice 6 follow-up: 3 durable duplicate-guard tests in
  `test_chat_composer.py`, 8/8 composer green.
- Differential proof (scratch `/tmp/diff_agent_model.js`, not
  committed): HEAD page originals (eval'd with a stubbed
  supplement) vs new module over a 179-case battery — pins,
  starred, row-match, pins-build, fetch gate, badge effort —
  **179/179 match, zero mismatches**.
- Focused (agent-model, composer, pins, codex-effort, bare-sticky,
  starred-removal, bubble-badge, badge-segments, starred-slash,
  agent-defaults): **107 passed, 1 failed** — the failure
  (`test_agent_defaults_post_rejects_capability_violations`) is on
  the pre-change list (401 auth-env, unrelated).
- Neighbors (slash-consistency, attachments, activity,
  attention-dots, followup-heal, stop-then-followup, pagination,
  history-search, action-forms, supervised ×2, restart ×2):
  190 passed; 7 failures all on the pre-change list (verified by
  identity grep). Asset-version failure output byte-identical
  pre/post (stale `20260927graphsGone` pin, fails since Slice 6).
- Broad with node on PATH (`/tmp/nodeshim`, pre-existing shim; no
  system node): post-change **1,815 passed, 28 failed, 60 skipped**;
  clean HEAD (`git stash -u`) **1,805 passed, 28 failed, 60
  skipped**; sorted FAILED identities **byte-identical (diff
  empty)** — +10 passed = the 10 new tests, zero regressions.
- Without node on PATH the pre-change tree gives 1,664 / 32 / 197
  (4 node-gated suites fail on missing binary); node presence, not
  this slice, accounts for the difference. Reported Slice 6 baseline
  (1,807 / 26 / 60) predates this environment; the 2-test delta is
  backend/env drift between machines — in THIS environment pre/post
  identities match exactly.
- `node --check` clean on all touched JS.
- Manual workflows: explicitly NOT performed — no browser or Flask
  round-trip available from this session (no UI served, no chat
  click-through). Node-level smoke of the exact decision points
  stands in: Enter/submit interpretation, plain/whitespace sends,
  attachment-only send, duplicate suppression, and
  agent/model/effort selection + fetch gate + badge effort are all
  executed under node by the committed suites above. No visible UI
  changes does not waive this: the workflow validation gap is
  recorded here for the external reviewer.

## Metrics

| Metric | Before (`be2658e9`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,396 | 25,386 (−10) | `wc -l` |
| Agent/Model decision fns needing page scope | ~6 (all) | 0 in module (explicit inputs) | grep |
| Agent/Model behavior tests | string pins + slices | +7 module tests; 1 suite repointed | pytest |
| Differential old-vs-new | — | 179/179 match | node harness |
| Full suite (node on PATH) | 1,805 / 28 / 60 | 1,815 / 28 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Supplement state, palette/badge DOM, catalog transport, prefs
   IO, sticky restore, badge construction, and send orchestration
   stay in the 25.4k-line page (correct per slice scope — they are
   the inputs/consumers of this boundary, not the decisions).
2. `restoreSessionStickySlash` deliberately untouched (two node
   harnesses execute the real page function); a future
   Messages/History slice may revisit restore with those harnesses.
3. The stale `test_chat_page_asset_versions_bump...` pin
   (`20260927graphsGone`) predates Slice 6 and still fails; not
   this slice's to fix, but it will keep failing until someone
   updates that pin.
4. No system `node` on PATH; `/tmp/nodeshim/node` (v18.18.2,
   pre-existing) used. 28 baseline failures remain untouched.
5. `test_codex_execution_passes_starred_effort_to_cli` fails both
   pre and post in this environment (backend/env); identical
   identity in the stash diff.

## Diff summary

- Added: `src/web/js/chat_agent_model.js` (169 L),
  `src/tests/test_chat_agent_model.py` (7 tests).
- Modified: `src/web/js/chat_page.js` (+46/−56: 6 spans rewired),
  `src/web/chat_page.html` (+2/−1: script tag, page `?v` bump),
  `src/tests/test_chat_composer.py` (+86: 3 Slice 6 follow-up
  tests), `src/tests/test_first_turn_agent_pins.py` (+10/−4:
  repoint to owned module),
  `docs/reviews/architecture-stabilization.md` (this section +
  Slice 6 approval boundary note).
- Deleted: no files.

## External Review Summary

1. **What changed architecturally?** Pin reads, starred
   lookups/row-match, `agent_pins` construction, the Codex pre-send
   fetch gate, and badge-effort resolution moved to owned pure
   `chat_agent_model.js`; the page keeps state, DOM, transport,
   prefs IO, sticky restore/override, badge construction, and
   send-path ordering behind same-signature adapters.
2. **What behavior intentionally changed?** Nothing. Differential
   179/179; adapters preserve signatures; send side-effect order
   untouched (sticky-then-pins attach; ensure-before-paint).
3. **What should be identical?** agent/model/effort selection,
   sticky-agent clearing, starred defaults, session restoration,
   outbound `agent_pins`/`sticky_agent` inputs,
   ensureCodexEffortForSend ordering, badge labels, Enter/send,
   attachment-only and duplicate-suppressed sends.
4. **What remains coupled or messy?** All DOM/IO/fetch in the page;
   restore stays pending a Messages/History revisit; 28 unrelated
   baseline failures remain; asset-version pin stale.
5. **What should be reviewed before the next chat domain?** The
   gather-then-delegate adapter shape (supplement snapshot in,
   pure decision out) as the pattern for Messages/History or
   Streaming; whether restore moves with its harnesses next.
6. **Is the next domain safe to begin?** This slice is
   self-contained (no Messages/History, Streaming, backend routing,
   or feature changes; failures identical to clean HEAD). Do NOT
   continue in this track until this review is approved. **STOP —
   Slice 8 not started.**

---

# Phase 3 — Slice 7 follow-up: execution-level duplicate-guard test
(correction to the Slice 6 regression protection)

## Review correction accepted

The Slice 7 structural pins (`test_ignore_duplicate_preserves_...
spans inside the duplicate branch) did NOT satisfy the requested
behavioral coverage: the original bug was `takePendingAttachments` /
`dismissOpenInteractiveCards` hoisted BEFORE the `ignore-duplicate`
branch, and a span-inside-the-branch test passes with that bug
present. Both structural tests are removed (no stale references
remain) and replaced by node-executed tests against the real
`sendMessage` below. No production behavior changed; no test-only
copy of `sendMessage` was created.

## Coverage (committed, `src/tests/test_chat_composer.py`)

- `test_send_message_duplicate_preserves_staged_attachments_and_cards`:
  extracts the REAL `async function sendMessage` from `chat_page.js`
  by exact markers (plus the real `takePendingAttachments` /
  `clearPendingAttachments` / `normalizeMessageContentForMatch`
  bodies) and executes it under node with stubbed page dependencies
  (DOM input, sendability + lane checks via the real
  `CuttleChatComposer` module, steer/queue/dismiss/send pipeline as
  tracking stubs). For duplicate in-flight AND duplicate queued
  sends with one staged attachment (`a.png`) and one open card:
  asserts take/dismiss/steer/enqueue/send/effort effects absent,
  staged attachment and card preserved, sticky re-resolve ran.
- `test_send_message_followup_takes_attachments_and_cards`
  (non-duplicate control): a real follow-up takes the staged
  attachment, dismisses cards, steers-or-queues, and sends nothing
  yet — proving the harness observes the take/dismiss path when it
  legitimately runs.
- Branch behavior clarified: duplicate sends DO clear composer text
  (input reset + sticky re-resolve) but must NOT clear staged
  attachments or open cards. Earlier "no clear" phrasing was
  ambiguous on this point; the committed assertions pin
  `inputValue == ""` alongside intact staged/card state.

## Mutation / discrimination evidence

- Temporary production mutation (NOT committed): inserted
  `takePendingAttachments()` + `dismissOpenInteractiveCards(...)`
  above the duplicate check in `src/web/js/chat_page.js`,
  reintroducing the original hoisting bug.
- Observed: `test_send_message_duplicate_...` FAILED
  (`assert 1 == 0` on take calls); the follow-up control also FAILED
  (`take == 2`, double-take) — the harness observes real ordering.
  Remaining 5 composer tests passed.
- Production restored via `git checkout -- src/web/js/chat_page.js`
  (committed `901bf260` content byte-identical; `git diff` shows no
  production delta); composer suite re-run 7/7 green.

## Results

- Focused + neighbors (agent-model, composer, pins, codex-effort,
  bare-sticky, starred-removal, bubble-badge, badge-segments,
  starred-slash, agent-defaults, slash-consistency, attachments,
  activity, attention-dots, followup-heal, stop-then-followup,
  pagination, history-search, action-forms, supervised ×2,
  restart ×2): **296 passed, 8 failed, 9 skipped** — all 8 failures
  identity-match the Slice 7 pre-change baseline list (7 neighbors
  + agent-defaults auth-env); zero new failures.
- Full broad suite NOT repeated: this follow-up touches only
  `src/tests/test_chat_composer.py` (test-only correction, zero
  production delta), and the Slice 7 broad gate (1,815 / 28 / 60,
  identities byte-identical to clean HEAD) still stands.
- No JS sources changed, so no `node --check` rerun was needed;
  the touched file is Python executed via pytest (7/7 green).

## Ownership note (preserved distinction)

Session restoration (`restoreSessionStickySlash`) stays in the page
in Slice 7 because of ownership/scope — restore is an orchestration
unit spanning prefs IO, chip render, typing-indicator badge refresh,
and shell live-status ordering (pinned as one ordered unit by the
working-bubble suite), not a pure decision over explicit inputs —
not merely because moving it would require repairing existing test
harnesses. A Messages/History slice may revisit restore with those
harnesses; the harness-repair cost is a consequence, not the reason.

## Limitations

- Manual browser/Flask verification still not performed (no UI
  served from this session); execution coverage is node-level with
  stubbed DOM/transport, as before.
- The harness stubs `composeMessageWithSlashChip` as identity and
  `healStaleGeneratingState` as noop (separately covered units);
  healing-fresh vs stale generating transitions are not
  re-exercised here.

## Diff summary (this follow-up)

- Modified: `src/tests/test_chat_composer.py` (structural pins
  replaced by 2 execution tests + marker-extracted harness),
  `docs/reviews/architecture-stabilization.md` (this section).
- Production files: unchanged (`git diff` empty for all
  non-test, non-docs paths).
- Commit: independent follow-up commit on main (see `git log`).
  No push, no restart. **STOP — Slice 8 not started.**

---

# Phase 3 — Slice 7 approval (recorded at the Slice 8 append boundary)

- Slice 7 (`901bf260`, agent/model controls) and its test-only
  follow-up (`18268ead`, execution-level duplicate-guard test) are
  **approved** per the worker brief for Slice 8; prior sections above
  are preserved verbatim — reviewer last read line 3580, and these
  notes sit at the append boundary rather than rewriting them.
- Slice 7 review follow-ups honored: (1) the Slice 6 regression
  protection is now node-executed against the real `sendMessage`
  (in-flight + queued duplicates preserve staged attachments/cards;
  follow-up control takes them; mutation proof observed fail then
  restored); the fix was test-only, zero production delta.
  (2) Duplicate-send branch behavior is now stated unambiguously:
  duplicates clear composer text but never staged attachments/cards.
  (3) Session-restoration deferral is recorded as ownership/scope
  (restore spans prefs IO + render + badge refresh + live-status
  ordering as one pinned unit), not harness-repair cost.

---

# Phase 3 — Slice 8: Messages/History, first sub-slice
(record normalization + history windowing)

## Phase status

- Slice: Phase 3 Slice 8 — message-record normalization + history
  windowing decisions → `src/web/js/chat_messages.js`
  (`window.CuttleChatMessages`). No subdivision beyond this was
  needed: the boundary is coherent, reviewable, and leaves
  display formatting, sync machinery, and panel/search UI for later.
- Git baseline before work: `18268ead` (Slice 7 follow-up); tree
  clean except pre-existing untracked `work/` (scratch, untouched).
- Git commit after work: the single `Phase 3 slice 8: messages/
  history records and windowing` commit on main (see `git log`).
- Completion status: **complete, awaiting Codex review**. Streaming
  / Slice 9 NOT started.

## Original problem

`chat_page.js` owned every message-record decision inline as
closures over page state: which metadata shape wins per record
(string/object/absent), report-URL precedence, timestamp/attachment/
usage delegation, project stamp resolution and backward fill,
visible-bubble counting, history dump windowing (server-paged vs
newest-page + buffer), page-meta application, and the
transcript-end predicate — interleaved with transcript DOM paint,
sync/poll machinery, persistence, session restore + ordering, and
the DOM-coupled predicates. Record/window logic had no direct
module tests (only structural pins and full-flow suites).

## Before implementation (inventory)

Messages/History responsibilities triaged (25,386-line page):

1. **Record normalization** (moved): `authMessageOptsFromServer`
   (30-field view over msg/meta/_project), `preferredModelFromMessages`.
2. **Project annotation** (moved resolve + fill, kept IO/DOM):
   `projectFromMessageRecord`, `annotateMessageProjects` (in-place
   backward fill); project-domain resolution stays in
   `CuttleChatProject`, injected as a callback.
3. **History windowing** (moved plan, kept application):
   `windowChatHistoryMessages` → `windowHistoryMessages` (pure
   paint/buffer/meta plan); `applyChatHistoryPageMeta` →
   `historyPageMeta`; `countVisibleChatMessages`; page globals +
   load-older indicator stay.
4. **Transcript predicate** (moved): `transcriptEndsWithAssistant`
   (pure); same-named adapter keeps the followup-heal span pin.
5. **Staying (explicit)**: transcript DOM paint/append/prepend/
   update/reorder, sync/poll machinery, persistence, session
   restore + ordering (untouched per the checklist rule),
   search/history-panel/prompt-history UI, streaming + live-status
   orchestration, DOM-coupled predicates
   (`thisTurnHasAssistantReply`, `transcriptEndsWithGenerationStop`
   with its DOM fallback, `uiAlreadyHasMessage`,
   `openTranscriptMissingThisTurnReply`), display formatting
   (`formatUserMessageForDisplay`), timestamp/usage parsers
   (injected as callbacks, not moved).
6. **Explicitly NOT absorbed**: backend routing, new features,
   dependency/preflight tooling. No backend changes. No paid
   (token-costing) smoke tests run — the touched paths are
   client-side record/window decisions fully covered under node.

Shared state the moved logic needed: timestamp/attachment/usage
helpers + project resolver + project lists (passed as an explicit
`deps` snapshot via one `messageRecordDeps()` gatherer — never live
page reads from the module); page size as a number; nothing else.
No moved function touches `document` / `window` / `localStorage` /
`fetch`.

## Changes made

- **Added `src/web/js/chat_messages.js`** (258 L): 8 pure
  functions behind `CuttleChatMessages` (classic script + node
  exports; TDZ-proof `globalThis` footer, same pattern as prior
  domain modules).
- **chat_page.js keeps**: all DOM/IO/sync/persistence/restore,
  plus thin same-signature adapters (pure delegations +
  gather-then-delegate for record deps; plan-apply for windowing).
  `windowChatHistoryMessages` keeps its non-array guard and
  indicator sync; `applyChatHistoryPageMeta` keeps its guard.
- chat_page.js: 25,386 → 25,306 lines (−80 net).
- `chat_page.html`: `chat_messages.js` script tag before
  `chat_page.js` (page `?v=20261001slice8`; domain tags keep slice
  versions).
- **Tests:** new `src/tests/test_chat_messages.py` (7 tests,
  node-executed against the real module + one adapter-delegation
  structural test). No existing suites needed repointing (only the
  followup-heal name/span pins, which the adapters preserve).
- Execution-level composer duplicate tests untouched and green.

## Architecture after

```
chat_page.js (globals, DOM paint, sync/poll, persistence, restore
              + ordering, indicators, orchestration)
      │  same-signature adapters / gather-then-delegate / plan-apply
      ▼
chat_messages.js (record opts + model recovery + project
                  annotate + visible count + window plan +
                  page meta + transcript-end) ──inject──▶ page
                  helpers (parseTs, attachments, usage) and
                  CuttleChatProject (resolution — never duplicated)
```

One-way dependency (page → namespace); module holds no state.

## Dependencies and state

- Removed: page closures over record building, model recovery,
  project fill, visible counting, window branching, meta
  application, transcript-end check.
- Introduced: `CuttleChatMessages` namespace (no new runtime deps).
  `oldestId` is `undefined` where the page must keep its value
  (empty dump) and `null` only where the page nulled it
  (non-finite id in a large window) — keep-vs-null preserved.
- Reverse deps: none. Callers (loadChatSession, sync paths, paint
  paths) unchanged — adapters preserve signatures and application
  order. Backend `/api/chat` untouched.

## Tests and verification

- New `test_chat_messages.py`: 7/7 (metadata shapes, report/
  attachment/usage precedence, model recovery, project
  resolve + backward fill, visible counting, paged/small/large/
  exact windows + standalone meta incl. keep-vs-null edges,
  transcript-end, module parses, delegation structure).
- Differential proof (scratch `/tmp/diff_messages.js`, not
  committed): HEAD originals (eval'd with stubbed helpers) vs new
  module + replicated adapter application over a 32-case battery —
  paint arrays AND resulting buffer/flag/count/id state —
  **32/32 match, zero mismatches**.
- Focused + neighbors (messages, composer, agent-model, pins,
  codex-effort, bare-sticky, starred-removal, bubble-badge,
  badge-segments, starred-slash, agent-defaults, message
  project-meta/share/pagination, history-search, find,
  followup-heal, stop-refresh, slash-consistency, attachments,
  activity, attention-dots, action-forms, supervised ×2,
  restart ×2, history-delete-modal): **305 passed, 10 failed** —
  all 10 identity-match the Slice 7 baseline list; zero new.
- Broad with node on PATH: post-change **1,821 passed, 28 failed,
  60 skipped**; clean HEAD (`18268ead`, via `git stash -u`)
  **1,814 passed, 28 failed, 60 skipped**; sorted FAILED
  identities **byte-identical (diff empty)** — +7 = new tests.
- `node --check` clean on all touched JS.
- Manual validation: not performed — no browser/Flask served from
  this session and no visual change exists to inspect; the gap is
  recorded, not waived. Node-level execution covers the moved
  decisions (record shapes, windowing branches, keep-vs-null
  edges) directly.

## Metrics

| Metric | Before (`18268ead`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,386 | 25,306 (−80) | `wc -l` |
| Record/window decision fns needing page scope | ~8 (all) | 0 in module (explicit inputs) | grep |
| Record/window behavior tests | none direct | +7 module tests | pytest |
| Differential old-vs-new (paint + state) | — | 32/32 match | node harness |
| Full suite (node on PATH) | 1,814 / 28 / 60 | 1,821 / 28 (identical list) / 60 | pytest + diff |

## Remaining concerns

1. Display formatting (`formatUserMessageForDisplay`, ~860 L with
   attachment/action-form/badge integration), sync/poll machinery,
   prompt-history nav, search + history-panel UI, and DOM-coupled
   predicates stay in the page — natural follow-up sub-slices that
   must each respect render/persistence/streaming ownership.
2. `restoreSessionStickySlash` + loadChatSession ordering untouched
   per the checklist rule; the windowing adapter it calls preserves
   the restore-before-remote-waiting order asserted by the
   working-bubble suite (green).
3. Stale `test_chat_page_asset_versions_bump...` pin and 28 baseline
   failures remain untouched (identical pre/post).
4. No system `node` on PATH; pre-existing `/tmp/nodeshim/node`
   (v18.18.2) used.

## Diff summary

- Added: `src/web/js/chat_messages.js` (258 L),
  `src/tests/test_chat_messages.py` (7 tests).
- Modified: `src/web/js/chat_page.js` (+38/−118: 8 spans rewired),
  `src/web/chat_page.html` (+2/−1: script tag, page `?v` bump),
  `docs/reviews/architecture-stabilization.md` (this section +
  Slice 7 approval boundary note).
- Deleted: no files. No existing test files modified.

## External Review Summary

1. **What changed architecturally?** Record normalization, model
   recovery, project annotation, visible counting, history
   windowing/meta, and the transcript-end predicate moved to owned
   pure `chat_messages.js`; the page keeps globals, DOM, sync,
   persistence, restore + ordering, indicators, and orchestration
   behind same-signature adapters.
2. **What behavior intentionally changed?** Nothing. Differential
   32/32 on paint + resulting state; callers and application order
   unchanged; keep-vs-null oldest-id semantics preserved.
3. **What should be identical?** History ordering/pagination,
   record normalization + metadata, attachment/action-form/agent
   badge integration points, search/history restoration,
   persistence semantics, caller contracts, duplicate-suppression
   execution behavior.
4. **What remains coupled or messy?** Display formatting, sync
   machinery, panel/search UI, DOM-coupled predicates in the
   25.3k-line page; 28 unrelated baseline failures; stale
   asset-version pin.
5. **What should be reviewed before the next sub-slice?** Whether
   the next Messages/History piece is display formatting
   (`formatUserMessageForDisplay`) or sync machinery — and its
   render/persistence/streaming seams — before any Streaming work.
6. **Is Streaming safe to begin?** No — not part of this slice.
   Do NOT continue past Messages/History until review approves.
   **STOP for Codex review — Slice 9 not started.**

---

# Phase 3 — Slice 8A approval + wording clarification
(recorded at the Slice 8B append boundary)

- `8058127b` is **approved as Phase 3 Slice 8A** — message-record
  normalization + history windowing — per the worker brief for
  Slice 8B. Prior sections above are preserved verbatim — reviewer
  last read line 3816, and these notes sit at the append boundary.
- Clarification appended, no rewrite: the Slice 8 review's "Slice 8
  complete / no subdivision" wording is superseded — only **8A** is
  complete. Messages/History remains open with the sub-slices
  inventoried below; the domain is NOT labeled complete while they
  remain.

---

# Phase 3 — Slice 8B: message display formatting / render planning

## Phase status

- Slice: Phase 3 Slice 8B — user-bubble display dispatch →
  `CuttleChatMessages.formatUserMessageForDisplay` (extends the 8A
  module; no new script tag — `chat_messages.js` already loads).
- Git baseline before work: `8058127b` (Slice 8A); tree clean
  except pre-existing untracked `work/` (scratch, untouched).
- Git commit after work: the single `Phase 3 slice 8B: user-bubble
  display dispatch` commit on main (see `git log`).
- Completion status: **complete, awaiting Codex review**. Further
  8C+ sub-slices and Streaming / Slice 9 NOT started.

## Inventory (actual, not estimated)

- `formatUserMessageForDisplay` is 54 lines (10381–10434), not ~860
  — the estimate counted following unrelated page functions. It is
  a pure dispatch tree with exactly one caller (the `addMessageToUI`
  paint path): no state, no DOM, no IO of its own.
- Inputs: `content` (nullable), `opts.attachments`,
  `opts.suppressInlineSlashChips`.
- Seams and owners: attachments decisions stay in
  `CuttleChatAttachments` (normalize/strip/infer — page adapters
  already delegate); slash parse stays in `CuttleChatSlash`
  (consumed via the page's `parseStoredSlashCommandMessage` adapter
  carrying pipeline/model resolvers); markdown (`formatMessage`,
  also paints assistant turns), attachment HTML
  (`buildMessageAttachmentsHtml`), form-reply/button leaves
  (`parseButtonClickFromContent`, `formatFormReplyHtml`), and slash
  chip leaves (`collapseCursorSlashChips`,
  `slashCommandChipHistoryHtml`, shared with badge/palette paths)
  stay in the page; `escapeHtmlInline` is a shared util.
- Boundary chosen: the module owns branch order only; all 11 leaf
  seams arrive as named, ownership-documented injected deps — not a
  generic bag, and nothing owned elsewhere was relocated. A whole-
  closure move was never needed (54 lines, zero reverse deps).

## Changes made

- `chat_messages.js`: +`formatUserMessageForDisplay(content, opts,
  deps)` verbatim (95 L with ownership docblock); header updated to
  name display dispatch + leaf-renderer ownership. Module 258 → 353 L.
- `chat_page.js`: 54-line body replaced by a same-signature
  gather-then-delegate adapter (−39 net; page 25,306 → 25,267).
- `chat_page.html`: unchanged (no new script).
- **Tests:** 3 rendered-output tests appended to
  `test_chat_messages.py`, executed against the REAL module + REAL
  attachments/slash modules + REAL extracted leaf bodies
  (escape, button parse + real labels, form-reply + real icons,
  attachment HTML + real mediaDownloadUrl); only markdown and chip
  HTML leaves are labeled stubs. Covers Selected/button (known,
  generic, XSS-escaped id)/form-selection/form-answers/slash
  chips+body/chips-only/suppressed-header/attachment-only
  placeholder hiding/text+attachment composition/empty/null/
  inferred-history-attachment. Repointed 1 pin
  (`test_cursor_chip_labels.py` canonical-collapse assertion now
  targets the owned module).
- Execution-level composer duplicate tests untouched and green;
  session-restore ordering untouched.

## Tests and verification

- New format tests failed 3/3 before the move (authentic failure),
  pass after; full messages + chip-label files 12/12.
- Differential proof (scratch `/tmp/diff_format.js`, not committed):
  HEAD page original vs module over a 23-case battery (plain,
  empty, null, whitespace, Selected/button/forms, slash variants
  incl. suppressed + `/model` + `/codex` + `/restart`, attachment
  Only/text/compose/empty-staged, inferred refs, trailing-button,
  bare `Selected:`) — **23/23 byte-identical outputs**.
- Focused message/chip/badge/composer run: 41 passed.
- Broad with node on PATH: post-change **1,824 passed, 28 failed,
  60 skipped**; clean HEAD (`8058127b`, via `git stash -u`)
  **1,821 passed, 28 failed, 60 skipped**; sorted FAILED
  identities **byte-identical (diff empty)** — +3 = new tests.
- `node --check` clean on both touched JS files. No paid
  (token-costing) tests run — touched paths are client-side
  render decisions fully covered under node.

## Metrics

| Metric | Before (`8058127b`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,306 | 25,267 (−39) | `wc -l` |
| Display-dispatch fns needing page scope | 1 | 0 (explicit deps) | grep |
| Rendered-output behavior tests | none | +3 (real module + real leaves) | pytest |
| Differential old-vs-new (exact HTML) | — | 23/23 match | node harness |
| Full suite (node on PATH) | 1,821 / 28 / 60 | 1,824 / 28 (identical list) / 60 | pytest + diff |

## Remaining Messages/History ownership work (inventoried)

1. **Assistant/markdown render path** (`formatMessage` + think/
   widget/vega activation, ~large, DOM-mounting) — next coherent
   boundary candidate: render planning vs DOM activation split.
2. **Message sync machinery** (poll, windowing application already
   owned, dedup `uiAlreadyHasMessage`, reconcile) — seams into
   persistence + Streaming; needs care.
3. **Prompt-history nav** (map IO stays; key handling + anchor
   application are composer-adjacent).
4. **Search + history-panel UI** (panel DOM, delete/rename modals,
   attention indicators — Activity-adjacent parts stay).
5. **DOM-coupled predicates** (`thisTurnHasAssistantReply`,
   `transcriptEndsWithGenerationStop`, `uiAlreadyHasMessage`,
   `openTranscriptMissingThisTurnReply`) — orchestration inputs,
   likely stay with explicit-input wrappers at most.
6. 28 baseline failures + stale asset-version pin remain untouched.

## Limitations

- Manual browser/Flask validation not performed (no UI served;
  no visual change to inspect) — recorded, not waived. Rendered
  HTML is asserted byte-exact under node except the two labeled
  stub leaves (markdown body, chip HTML), whose own units own
  their internals.
- The harness pins real leaf bodies by exact source markers; a
  leaf rename breaks the harness loudly (intended).

## Diff summary

- Modified: `src/web/js/chat_messages.js` (+102: formatter + docs),
  `src/web/js/chat_page.js` (+13/−52: adapter),
  `src/tests/test_chat_messages.py` (+139: harness + 3 tests),
  `src/tests/test_cursor_chip_labels.py` (+6/−1: repoint),
  `docs/reviews/architecture-stabilization.md` (this section +
  8A approval/clarification).
- Added/deleted files: none. `chat_page.html` untouched.
- Commit independently on main. No push, no restart.
  **STOP before further sub-slices or Streaming.**

---

# Phase 3 — Slice 8B approval (recorded at the Slice 8C append boundary)

- `adcce8c5` (Slice 8B, user-bubble display dispatch) is **approved**
  per the worker brief for Slice 8C; prior sections above are
  preserved verbatim — reviewer last read line 3963, and these notes
  sit at the append boundary.
- Carry-forward fix honored in this commit: 8B changed the already-
  loaded `chat_messages.js` + `chat_page.js` without bumping their
  `?v=` fingerprints, so `?v=`-immutable clients (7-day max-age per
  `src/api/web_chat_api.py` `add_cache_headers`: fingerprinted
  assets immutable, HTML no-store) would keep running 8A code after
  an 8B deploy. Both assets are bumped to `?v=20261001slice8c` in
  this commit (`chat_page.html` only; it is the sole entry point
  loading them). No unrelated stale pins touched.

---

# Phase 3 — Slice 8C: structured-block planning + vega/copy leaves

## Phase status

- Slice: Phase 3 Slice 8C — pre-escape structured-block extraction
  (think/tool/trace/progress/meters/pricing/terminal/media/vega) +
  placeholder restore protocol + vega-wrap/copy-text leaves →
  `chat_messages.js`. No new script tag (module already loads).
- Git baseline before work: `adcce8c5` (Slice 8B); tree clean
  except pre-existing untracked `work/` (scratch, untouched).
- Completion status: **complete, awaiting Codex review**. 8D and
  Streaming / Slice 9 NOT started.

## Boundary chosen (and what was NOT moved)

`formatMessage` is a ~1,200-line staged pipeline; relocating it
whole was rejected as monolith-shifting. The owned seam is
**pre-escape extraction → placeholder → post-render restore**:
- Moved (pure HTML planning, verbatim): think (code-fence-aware,
  incl. `formatThinkingInner`/`formatThinkingLineInline` which have
  no other callers), tool_output, trace, progress (needs `clamp`),
  meters, pricing (needs `renderCuttlePricingHtml` leaf), terminal,
  media (needs `mediaKindFromUrl` + `buildMediaThumbHtml` leaves),
  vega tag+fence (incl. `buildVegaWrapHtml` vega planning),
  `cleanCodeCopyText` (pure; page adapter kept for the copy-button
  wiring), and the 9-prefix restore loop table.
- New module fns: `extractHeadStructuredBlocks` (think+tool),
  `extractTailStructuredBlocks` (trace/progress/meters/pricing/
  terminal/media/vega), `restoreStructuredBlocks`,
  `formatThinkingLineInline`, `formatThinkingInner`,
  `buildVegaWrapHtml`, `cleanCodeCopyText`. Deps (6 named leaves):
  `escapeHtmlInline`, `clamp`, `renderMdLinkChip`,
  `renderCuttlePricingHtml`, `mediaKindFromUrl`,
  `buildMediaThumbHtml`.
- Staying, explicitly: supervised-activity extraction + restore
  (reads live `window.CuttleSupervised` state), widget ingest (side-
  effecting `queueClientWidgetUpsert`), buttons/action-forms/
  cuttle_form extraction (action-form machinery + window state),
  markdown images/links/code/git-SHA core, the escaped line loop,
  ALL DOM activation (`activateEnhancements`, vega embed, code-copy
  buttons, hljs, terminal/button wiring), persistence, sync,
  streaming.
- Order-exactness: the page calls head-extract → supervised
  (unchanged, inline) → tail-extract, preserving the original
  pipeline order so live-activity JSON can never match a block
  pattern. Restore order across prefixes is irrelevant (distinct
  inert placeholders); the single restore call sits at the first
  restore site, supervised/forms/code/link loops stay inline.
- Feature task now navigable in the owner: "fix a collapsible
  block's extraction/escaping/placeholder round-trip" (think
  truncation, meter JSON fallbacks, terminal attr escaping, vega
  wrap shape) without touching the 1,200-line pipeline; the
  planning↔activation contract (`data-vega-spec` in → embed/error/
  skip behavior) is pinned by test.

## Changes made

- `chat_messages.js`: +461 (planning fns + ownership docblocks);
  module 353 → 814 L.
- `chat_page.js`: +25/−427 — think-inner/line/vega-wrap bodies
  deleted (no other callers), copy-text/vega-wrap same-signature
  adapters, `structuredRenderDeps()` gatherer, head/tail/restore
  call substitutions; page 25,267 → 24,865 (−402 net).
- `chat_page.html`: `?v=20261001slice8c` for both chat assets
  (carry-forward fix; no other entry point loads them).
- **Tests:** 4 rendered-output tests (extract/escape/round-trip,
  attrs/malformed/disabled fallbacks, vega wrap + REAL
  `activateVegaEmbeds` execution on a fake DOM, restore + copy
  text) + 1 page-order test (head < supervised < tail; moved bodies
  absent; supervised stays). 3 wiring pins repointed at the owned
  module (`cursor_agent_slash_commands`, `agent_cost_slash`,
  `agent_usage_slash`) with page-wiring assertions kept.
- Composer duplicate execution tests, history windowing, session-
  restore ordering untouched and green.

## Tests and verification

- 4 new tests failed pre-move (authentic failure), pass post-move;
  messages file 15/15.
- Differential proof (scratch `/tmp/diff_structured.js`, not
  committed): HEAD original spans (eval'd) vs module over
  head/tail/full-pipeline-restore + wrap/copytext, 16 adversarial
  cases (fenced/inline-code think protection, unclosed think,
  redacted_thinking, XSS content/attrs, clamped percents, NaN pct,
  malformed meters JSON, disabled rows, locked∩interactive
  terminal, missing media src, fake placeholders, null/empty) —
  **51/51 match, zero mismatches**.
- Earlier `/tmp/diff_format.js` (8B, 23/23) unaffected by this
  slice (formatter untouched).
- Focused + neighbors (messages, chips, composer, badges, widgets,
  agent-model, codex-effort, pins, starred-removal, bubble-badge,
  followup-heal, stop-refresh, history-search, pagination,
  project-meta/share, supervised ×2, action-forms, attachments,
  activity, restart ×2, slash-consistency, pricing, cost/usage/
  cursor-slash): 331+ passed; every failure identity-matched to
  the 8B baseline except 3 same-family wiring pins, which were
  repointed (intent preserved) and are green.
- Broad with node on PATH: post-change **1,829 passed, 28 failed,
  60 skipped**; 8B HEAD **1,821 / 28 / 60**; sorted FAILED
  identities **byte-identical (diff empty)** — +8 = 3 format (8B)
  + 5 structured/order (8C).
- `node --check` clean on both touched JS files. Transport note:
  mid-slice audit caught `\uXXXX`-in-`find` mangling plus doubled
  `\\u` escapes in new code; repaired by byte-exact replacement
  and re-verified (zero stray control bytes; differential green
  after repair). No paid tests run.

## Metrics

| Metric | Before (`adcce8c5`) | After | Method |
|---|---|---|---|
| `chat_page.js` lines | 25,267 | 24,865 (−402) | `wc -l` |
| `chat_messages.js` lines | 353 | 814 (+461) | `wc -l` |
| Structured planning tests | none | +4 behavior, +1 order | pytest |
| Differential (extract+restore) | — | 51/51 match | node harness |
| Full suite (node on PATH) | 1,821 / 28 / 60 | 1,829 / 28 (identical list) / 60 | pytest + diff |

## Remaining Messages/History work (acceptance-criteria disposition)

- **Sync machinery** (`syncSessionMessagesFromServer`, dedup,
  reconcile, poll): done when record/window owners are its only
  message-shape deps and streaming owns live rows — still open.
- **Prompt-history nav**: done when key handling/anchor/persist
  split is tested — still open (composer-adjacent).
- **Search + history-panel UI**: done when panel DOM/persistence
  actions route through owned record/window fns — still open.
- **Assistant markdown core** (links/code/tables/line loop,
  `formatMessage` remainder): done when line rendering is
  separable from block planning without duplicating escape
  semantics — still open; NOT to be moved whole.
- **DOM activation** (vega embed, copy buttons, hljs, terminal/
  button wiring): done when a bounded activation owner with
  injected container-scoped deps replaces inline wiring — still
  open; this slice pins its input contract only.
- Domain closure requires 8D-or-later disposition of all five;
  none is an optional extra extraction — each has the criterion above.

## Limitations and risks

- Manual browser/Flask validation not performed (no UI served;
  no visual change to inspect) — recorded, not waived. Node-level
  execution covers planning HTML byte-exact plus real vega
  activation transitions; button wiring + hljs + terminal wiring
  stay browser-only and unexercised.
- Markdown/chip stub leaves in the 8B harness remain premises of
  their own units.
- 28 baseline failures + stale asset-version pin untouched.
- Page-side `structuredRenderDeps()` is a 6-leaf gatherer, not a
  generic bag: each leaf is named, single-purpose, and stays in
  its owner (util/pricing/media/link-chip). Watch for growth in
  later sub-slices.

## Diff summary

- Modified: `src/web/js/chat_messages.js` (+461),
  `src/web/js/chat_page.js` (+25/−427),
  `src/web/chat_page.html` (+2/−2: `?v` bumps),
  `src/tests/test_chat_messages.py` (+206: harnesses + 5 tests),
  `src/tests/test_cursor_agent_slash_commands.py`,
  `src/tests/test_agent_cost_slash.py`,
  `src/tests/test_agent_usage_slash.py` (wiring repoints),
  `docs/reviews/architecture-stabilization.md` (this section +
  8B approval + cache-fix note).
- Added/deleted files: none.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before 8D or Streaming.**

## Baseline reconciliation (8B vs 8C, same-environment) — clarification

- 8B report's `adcce8c5: 1824 passed / 28 failed / 60 skipped` and the 8C
  report's "8B HEAD 1821" differ because they were taken on different trees:
  the 1824 figure was the 8B worktree run, the 1821 figure the 8A baseline
  the 8C author first compared against. Neither was a same-environment
  8B-vs-8C comparison, and the "8 = 3 8B tests + 5 8C tests" split
  mislabeled pre-existing tests as new. Correction: the true
  same-environment, same-invocation comparison is HEAD (`f4ba9cd1`, the 8C
  tip) broad minus `src/tests/unit` (whose `test_security.py` collection
  error, `ModuleNotFoundError: core.multi_stage_processor`, is pre-existing
  on HEAD and unrelated to this domain): **28 failed / 1814 passed /
  79 skipped**, with all 28 FAILED identities recorded in
  `/tmp/failed_head.txt` (kept out of tree). The earlier 1829/60 figures
  came from a different invocation scope; the 1814/79 figures above are the
  controlled baseline the 8D gate below is diffed against.
- Paid-prompt audit: no smoke/e2e prompt-sending tests were run for 8D.
  Verification is node fake-DOM harnesses + the repo pytest suite only;
  `src/tests/spend_guard.py` reports zero "SPENDING REAL TOKENS". No paid
  or token-consuming test was executed or repeated.

## 8C approval (recorded post-reconciliation)

- 8C `f4ba9cd1` (structured-block boundary + cache fixes) is accepted on the
  reconciled baseline above: same-environment HEAD broad shows the 28
  pre-existing failures with no slice regression, and the `?v` fingerprint
  bumps for the already-loaded assets were verified in `chat_page.html`.
- Approval recorded here at the append boundary; no prior section rewritten.

## Slice 8D report — markdown code/link extraction + bounded activation seam

- Owned boundary (narrow, explicit inputs):
  `CuttleChatMessages.extractCodeLinkBlocks(text, { escapeHtmlInline,
  renderMdLinkChip, isSafeMdHref })` → `{ text, blocks: { code, link } }`
  owns fenced-code + markdown/bare-link extraction (pure HTML planning; head/
  tail extraction already owned). `src/web/js/chat_activate.js`
  (`CuttleChatActivate`, IIFE + `module.exports` dual export, zero
  `window`/`document`/`navigator`/`setTimeout`/icon references) owns the
  bounded post-paint activation seam: `activateVegaEmbeds(containerEl,
  deps)` and `attachCodeCopyButtons(containerEl, deps)` with explicit dep
  injection (`vegaEmbed`, `createElement`, `copyIcon`/`copiedIcon`,
  `copyText`, `notifyError`, `later`). Supervised-activity, widgets,
  action-forms/buttons, markdown tables, and the main line loop stay in the
  page for the next boundary (8E candidate).
- Page adapters stay with identical signatures: vega/copy activation
  adapters supply `window.vegaEmbed`, `document.createElement`,
  `COPY_ICON`/`COPIED_ICON`, clipboard, toast, `setTimeout`; code/link
  extraction replaced by the module call with the live `linkChips` array
  bound for the handle/git linking stages below it.
- Restore pass order pinned byte-exact: the bulk
  `restoreStructuredBlocks` call redacts code/link
  (`Object.assign({}, structuredBlocks, { code: [], link: [] })`) and the
  page restores code then link after forms/buttons exactly as pre-change,
  so literal `{{CUTTLE_FORM_0}}`-style tokens inside fenced code are never
  substituted (and the reverse HEAD-faithful direction is preserved).
  `structuredBlocks` remains the live owner; only pass order is pinned.
- Differentials: module extract vs HEAD page span, 16 inputs × 3 channels
  (text/code/link) byte-identical (`/tmp/diff_codelink.js`, scratch only);
  `null` input is new-tolerant in the module (page threw; unreachable —
  `formatMessage` coerces upstream) and is pinned as hardening, not as
  equivalence. Activation functions diffed identical modulo the declared
  dep rewiring (plus a brace-balance slicer artifact in the scratch
  script; both files `node --check` clean).
- Tests (all node-executed real module/page spans, committed):
  `test_chat_activate.py` (5: vega embed-error/skip paths, no-library
  noop, copy-button lifecycle incl. idempotence/click/re-arm/failure,
  module parse, versioned script-tag + load order) and
  `test_chat_messages.py` (+4: code-fence planning, link chips/safety,
  full-pipeline interleave, restore-pass-order adversarial interleave
  executing the real page extract + restore spans). Three 8C-era wiring
  pins (`test_agent_cost_slash`, `test_agent_usage_slash`,
  `test_cursor_agent_slash_commands`) updated to the redacted restore call
  shape — same contract (page wired through the restore protocol), new
  call text; behavioral equivalence carried by the order test.
- Gates, same invocation (`--ignore=src/tests/unit`, pre-existing
  collection error): focused/neighbor suites green (24/24 messages +
  activate); broad **28 failed / 1823 passed / 79 skipped** with FAILED
  identities byte-identical to HEAD (`diff` clean; +9 passed = 8 new tests
  + 1 rename, exactly one test ID removed). The 5 neighbor failures in
  `test_chat_attachments.py` / `test_chat_history_search.py` (incl. the
  stale `20260927graphsGone` asset pin) reproduce identically on HEAD —
  pre-existing, untouched. `node --check` clean for all touched JS.
- Cache: `chat_page.html` bumps `chat_messages.js` and `chat_page.js` to
  `?v=20261001slice8d` and adds versioned `chat_activate.js?v=
  20261001slice8d` (messages < activate < page order); `?v`-fingerprinted
  `/js/` assets serve immutable, so existing clients refetch.
- Files: `src/web/js/chat_page.js` (−~110: extraction + activation moved
  out, order-pin comment + code/link loops), `src/web/js/chat_messages.js`
  (+57: `extractCodeLinkBlocks`), `src/web/js/chat_activate.js` (new),
  `src/web/chat_page.html` (+3/−2: `?v` bumps + new tag),
  `src/tests/test_chat_messages.py` (+263: harnesses + order test),
  `src/tests/test_chat_activate.py` (new), 3 wiring-pin updates, this
  section. No backend, routing, or product-behavior changes.
- Remaining Messages/History scope for the next boundary: markdown table
  restore + main line loop (stays in page; the risky move 8D explicitly
  declined), sync/history navigation/search/panel seam disposition with
  ownership-based acceptance criteria. Session restore still deferred for
  ownership/scope reasons. Manual browser/Flask validation unavailable
  (node/fake-DOM only) — reported, not waived.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before 8E or Streaming.**

## Verification correction — controlled 8B/8C/8D comparison (report-only, no production change)

- Method (read-only to the user worktree; no stash of user edits at any
  point): detached `git worktree` checkouts at exactly `adcce8c5` (8B),
  `f4ba9cd1` (8C), `a4981c0e` (8D) under `/tmp/ctrl8d` (removed after
  evidence capture; manifests/logs retained there as scratch, not in
  tree). Identical invocation everywhere: main-repo `.venv` python,
  `PATH` with the node shim, `pytest -q -p no:warnings src/tests/
  --ignore=src/tests/unit`, cwd = tree root. `CUTTLE_AGENT_SMOKE` and
  `CUTTLE_ALLOW_SPEND` unset (verified empty). No paid-test selectors
  were needed: prompt-spending tests self-skip by default (see spend
  audit below), so scope was full-minus-unit with unchanged collection.
- Unit exclusion, proven per baseline: `pytest src/tests/unit
  --collect-only` on EACH of the three trees reports the identical
  pre-existing error — `test_security.py`:
  `ModuleNotFoundError: No module named 'core.multi_stage_processor'`
  (with a `multi_stage_processor` fallback miss), `29 tests collected,
  1 error`. A plain `pytest src/tests/` therefore collects nothing on ANY
  baseline (collection error interrupts the run), so every historical
  broad figure necessarily excluded unit somehow; `--ignore=src/tests/unit`
  is the uniform documented scope. Fixing the import is deferred
  dependency/preflight work, out of slice scope; the breakage is
  domain-unrelated (agent harness CLP import, not Messages/History).
- Manifest diffs (collect-only IDs, same scope): 8B 1916 → 8C 1921 → 8D
  1930. 8B→8C: +5 test IDs, −0 (the five 8C structured/vega tests; the 8C
  report's "8 = 3 8B + 5 8C" split was wrong — 8C added exactly 5).
  8C→8D: +10 IDs, −1 ID. The 10 added = 9 brand-new tests (5
  `test_chat_activate.py`, plus `test_code_fence_planning_escapes_and_
  protects`, `test_format_message_pipeline_interleaves_all_stages`,
  `test_link_planning_chips_and_safety`,
  `test_restore_pass_order_pinned_against_placeholder_interleaving`) + 1
  rename (`test_vega_wrap_planning_and_activation_order` →
  `test_vega_wrap_planning`); the 1 removed is the rename's old name.
  Corrected accounting: 8D's "+9 passed" = +10 added passing −1 removed
  passing (the old vega test passed on 8C), NOT "8 new + 1 rename adding
  a pass" — a rename is pass-neutral; the arithmetic closes exactly
  (see counts below).
- Controlled broad results (same invocation, worktree conditions):
  8B `39 failed / 1797 passed / 80 skipped`; 8C `39 failed / 1802 passed
  / 80 skipped`; 8D `39 failed / 1811 passed / 80 skipped`.
  Arithmetic closes on all three (collected − failed − skipped =
  passed). FAILED-identity diffs are EMPTY in both directions:
  8B≡8C and 8C≡8D byte-identical sets. **8C did not regress from 8B;
  8D did not regress from 8C.** Pass deltas (+5, then +9 net) equal new
  test IDs exactly. No slice regression exists, so no production fix was
  made — this correction is report-only.
- Worktree-vs-main gap (same commit f4ba9cd1 or a4981c0e, different
  environment): main tree shows 28 failed / 79 skipped; worktrees show
  39 failed / 80 skipped. Causes, evidenced: (1) worktrees lack ignored
  untracked files — principally `src/.env` (gitignored, present on
  main, absent in every worktree) — plus no in-tree `.venv`
  (`test_restart_daemon_sh_dry_run` fails with `Missing venv python:
  .../wt8c/.venv/bin/python`, a pure checkout artifact); (2) 14
  worktree-only failures are env/checkout-path sensitive (9
  `test_agent_context_all_clis` compact tests failing on
  `Invalid working directory: C:/Projects/Cuttle`, router/eval, git
  pending-changes, mobile webview, restart-daemon-sh, runtime-paths).
  25 of the main-tree 28 fail in worktrees too; 3 main-only failures
  (`test_health_api_round_trip`, 2× `test_ui_layout_*`) flip the other
  way (live-daemon/timing sensitive) — environmental both directions,
  identical across all three slices. Skip delta 79↔80 is exactly one
  test: `test_muse_chat_discovery.py:78` passes on main (chat db present)
  and skips in worktrees (`no chat database on this machine yet` — the
  db path resolves per-checkout); pass/skip arithmetic closes
  (11 extra failures + 1 extra skip = 12 fewer passes: 1823→1811).
  Older loose figures (1824/1821/1829 passed, 60 skipped) carry unknown
  invocation scope and are non-comparable; the controlled numbers above
  supersede them for gate purposes.
- Spend audit: zero selectors, unchanged collection; the suite's live
  tests self-skipped in every broad run (`test_agent_harness_smoke.py`
  ×3 + `test_agent_resume_contract.py::...live...` with explicit
  `SPENDING REAL TOKENS` skip reasons; `CUTTLE_AGENT_SMOKE`/
  `CUTTLE_ALLOW_SPEND` unset); zero `SPENDING REAL TOKENS` guard trips
  in any log. No paid/token prompt test was executed or repeated.
- Pipeline-harness scope verdict:
  `test_format_message_pipeline_interleaves_all_stages` DOES execute the
  real complete page `formatMessage(text)` span end-to-end (span-extracted
  from `CHAT_PAGE_JS`, not reimplemented), with the real
  `CuttleChatMessages` and `CuttleChatAttachments` modules and real page
  leaf spans; the message exercises think/code/links/supervised/trace/
  progress/tool stages and asserts restore interleave order, zero
  leftover link placeholders, think-escaping, single-block, and
  handle+git stage invocation. Honest stub limits (documented
  in-harness): DOM-backed `escapeHtml` runs as the inline-escape
  equivalent (escape degrees unit-covered elsewhere, not
  pipeline-covered); widget ingest, handle/git linkify, supervised
  builders, pricing/media leaves are canned/identity with call
  recorders — so action-form card CONTENT and handle-chip OUTPUT are
  invocation-verified, not output-verified, in this test. The ORDER test
  likewise executes the real page extract call-site + restore-tail spans
  with the real module. One reporting correction: the 8D report's "all
  node-executed" overstates one test —
  `test_format_message_orders_structured_extraction_around_supervised`
  is a structural source-order pin (head < supervised < tail, no
  leftover inline loops), not a node execution; it complements, not
  duplicates, the executed pipeline test.
- No reviewer approval is recorded here: approval is the reviewer's act
  on this evidence. Factual gate status: no regression on any
  slice-to-slice comparison under controlled conditions.
- Evidence kept out of tree in `/tmp/ctrl8d`: per-tree ID manifests,
  `-rf` FAILED identity files (with pairwise `diff`s), `-rs` skip
  summaries, full broad logs, exact commands above. No huge logs are
  reproduced in this review file.
- Git: this correction appends report-only text; no production files
  touched. Commit independently; no push, no restart.
  **STOP for Codex review; no 8E/Streaming.**

## Prior approval recorded: 8C f4ba9cd1, 8D a4981c0e, correction 2e2c158d

- External review approves 8C, 8D, and the controlled-comparison correction
  on exact-tree same-command evidence, with continued verification
  discipline (exact revisions/command/env/scope, IDs, failure comparisons;
  no unknown-scope historical counts). Reviewer last read line 4358;
  this section appends only beyond that boundary; earlier sections
  preserved.

## Slice 8E report — markdown rendering ownership + activation disposition

- New owner `src/web/js/chat_markdown.js` (`CuttleChatMarkdown`, IIFE +
  `module.exports`, zero DOM/state): `formatInlineMarkdown` (pure inline
  spans), `split/is/looksLike` table-row planning,
  `markdownTableAlignFromSep`, `renderMarkdownTableHtml`, and
  `renderMarkdownBlocks(text)` (the ~180-line block loop: empty-line
  collapsing, block-placeholder passthrough, headers, rules, GFM tables,
  blockquotes/callouts, lists, paragraphs). Takes already-escaped text,
  returns joined HTML; page keeps `escapeHtml`, placeholder restore order,
  and DOM activation. `BLOCK_PLACEHOLDER_RE` lives with the loop as the
  routing table; whoever mints a new block placeholder kind must extend
  it (documented in the module header). `chat_messages.js` does NOT grow:
  871 → 871 lines; new module is 300 lines.
- Activation disposition: `highlightCodeBlocks(containerEl, { hljs })`
  and `wireTerminalInputs(containerEl, { sendMessage })` move into
  `chat_activate.js` (same container-scoped idempotent-flag seam, narrow
  deps; `processMessage` injected, never imported). Page
  `activateEnhancements` keeps orchestration + order + try/catch, now
  delegating those two stages. Deliberate stays with ownership reasons:
  cuttle-button wiring (button domain + `isLoading`/
  `lockCuttleButtonsInContainer` page state), supervised-card wiring
  (supervised owner), form/action-form/widget wiring (form/widget
  owners) — moving them would drag execution orchestration into the
  seam. `renderCuttlePricingHtml` stays in the page (pricing owner per
  8C pins) and now calls
  `CuttleChatMarkdown.formatInlineMarkdown` for note cells.
- Page adapters keep signatures; call sites replaced:
  `renderMarkdownBlocks`, pricing inline, hljs/terminal delegation.
  Net page delta −302/+~20 lines in the touched regions.
- Coverage (committed, behavioral, extended before/around moves):
  pipeline MSG2 added pre-move (headers/lists/table/quote/rule/
  placeholders through real `formatMessage`), green before extraction;
  new `test_chat_markdown.py` (7 tests: real module execution over
  inline edges, table planning/render incl. ragged rows, block loop
  structures, placeholder/fake-token passthrough, tag pin);
  `test_chat_activate.py` +2 (highlight order/safety/no-lib/throw,
  terminal send/trim/clear/keys/idempotence on fake DOM). Pricing
  renderer test repointed to the real module function (same contract).
- Differentials vs pre-change `a4981c0e`: module fns byte-identical to
  page spans (inline 12 + row helpers 10×3 + align 6 + table render 3 +
  block loop 30 inputs; `/tmp/diff_markdown.js` scratch); moved
  highlight/terminal fns identical modulo declared dep rewiring;
  complete-pipeline MSG output byte-identical (2355 chars, positions
  identical; old-revision output captured from an `a4981c0e` worktree,
  removed after). `null` hardening: none added this slice (loop takes
  strings as the page always passed).
- Gates, same controlled scope (`--ignore=src/tests/unit`, spend flags
  verified unset, zero `SPENDING REAL TOKENS` trips): focused 33/33
  (markdown + messages + activate); neighbors 196 passed with only the
  5 pre-existing failures (byte-identical IDs to the 8D HEAD check);
  broad **28 failed / 1832 passed / 79 skipped** with FAILED identities
  byte-identical to the 8D main-tree 28 (`diff` clean; +9 passed = 9 new
  tests). `node --check` clean on all touched JS.
- Cache: `chat_page.html` adds versioned `chat_markdown.js?v=
  20261001slice8e` (messages < activate < markdown < page) and bumps
  `chat_page.js` + `chat_activate.js` to `slice8e`; `chat_messages.js`
  unchanged, keeps its `slice8d` fingerprint (correct: fingerprint
  matches content).
- Files: `src/web/js/chat_markdown.js` (new), `src/web/js/chat_page.js`
  (−~280: helpers/loop/highlight/terminal out, delegations in),
  `src/web/js/chat_activate.js` (+38), `src/web/chat_page.html`
  (+4/−3 tags), `src/tests/test_chat_markdown.py` (new),
  `test_chat_activate.py` (+75), `test_chat_messages.py` (+31 pipeline
  MSG2 + module require), `test_agent_cost_slash.py` (+9 pricing
  repoint), this section. No backend/routing/product-behavior changes.
- Remaining Messages/History inventory with next-boundary proposal (no
  domain-closure claim): prompt-history navigation (`promptHistory[]`
  state ~3021–3190, `navigatePromptHistory`, `handlePromptHistoryKeyDown`
  3270) is the next coherent boundary — pure index/anchor decisions
  with explicit state in/out, DOM/persistence staying in page/composer;
  history search keeps page query state (`historySearchLiveQuery`) with
  matching owned by `chat_find.js` (existing module, gate tests as
  contract); `loadChatHistory`/poll/session-restore/sync stay with
  streaming orchestration (Slice 9 scope, not this domain); session
  restore still deferred for ownership/scope reasons. Suggested 8F:
  prompt-history navigation ownership. Manual browser/Flask validation
  unavailable (node/fake-DOM only) — reported, not waived.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before 8F/next portion or Streaming.**

## Prior approval recorded: Slice 8E 21537f00

- External review approves 8E. Reviewer last read line 4448; this
  section appends only beyond that boundary; earlier sections preserved.

## Slice 8F report — prompt-history navigation ownership + closure review

- New owner `src/web/js/chat_prompt_history.js`
  (`CuttlePromptHistory`, IIFE + `module.exports`, zero DOM/storage):
  single browse-state shape `{ list, index, draft }` (`createState`),
  `appendRecord` (trim/dedupe-newest/cap-100/browse-reset, bool),
  `captureDraft` (sticks only on fresh browse), `step` (older/newer
  index machine returning `none`/`oldest`/`text`/`draft`),
  `mergeSessionHistories` (longer stored side wins, live fallback,
  cap), `deriveFromUserMessages` (user-role trim/filter/cap),
  `atStartAnchor`/`atEndAnchor` caret predicates. The three page-scope
  `let`s collapse into one `promptHistoryState` instance — state shape
  and transitions owned in exactly one place, never split.
- Page keeps: the state instance, all localStorage IO + session-id
  wiring (`read/writePromptHistoryMap`, load/migrate/persist shells),
  DOM (`applyHistoryTextToTextarea`, caret reads, keydown glue, typing
  listeners), slash integration (draft composition via
  `composeMessageWithSlashChip`, chip clearing). `navigatePromptHistory`
  keeps its signature and consumed semantics; capture/apply stay
  page-side. Typing in either composer calls `resetBrowse` (draft-clear
  is unobservable: the next older-step recaptures since index is -1).
  Five `recordPromptHistory` send-path call sites unchanged (persist
  fires only on real appends, as before).
- Coverage (committed, real module execution): new
  `test_chat_prompt_history.py` (7 tests: record trim/dedupe/cap incl.
  105-item overflow, full browse cycle both directions with draft
  restore, single/empty/fresh-down edges, draft-capture-once, all six
  merge choices, derivation, anchor table, tag pin). Pre-move
  differential `/tmp/diff_prompt.js` (scratch): real page spans vs
  module over record/browse/migrate/derive/anchor vectors — IDENTICAL
  throughout, plus one documented hardening: a null transcript entry
  used to trip the page try/catch and drop the WHOLE derivation, the
  module skips nulls and recovers the rest (pinned as hardening, not
  equivalence — same precedent as 8D null tolerance).
- Gates, same controlled scope (`--ignore=src/tests/unit`, spend flags
  verified unset, zero `SPENDING REAL TOKENS` trips): focused +
  neighbors 242 passed with only the 5 known pre-existing failures
  (composer duplicate, steer, starred-removal all green); broad **28
  failed / 1839 passed / 79 skipped** with FAILED identities
  byte-identical to the 8D/8E 28 (`diff` clean; +7 passed = 7 new
  tests). `node --check` clean on all touched JS.
- Cache: `chat_page.html` adds versioned
  `chat_prompt_history.js?v=20261001slice8f` (after markdown, before
  page) and bumps `chat_page.js` to `slice8f`; all other assets
  untouched with matching fingerprints.
- Files: `src/web/js/chat_prompt_history.js` (new),
  `src/web/js/chat_page.js` (−~90: split state + five inline decision
  bodies out, one state object + delegations in),
  `src/web/chat_page.html` (+2/−1 tags),
  `src/tests/test_chat_prompt_history.py` (new), this section. No
  backend/routing/product-behavior changes.

## Messages/History closure status (against the Phase 3 goal)

- Owned and tested: message records (`chat_messages.js`: normalize,
  dispatch, windowing, pagination), user/assistant rendering
  (extract planning + restore protocol), markdown (`chat_markdown.js`:
  inline/tables/block loop), activation (`chat_activate.js`: vega,
  copy, highlight, terminal), prompt navigation
  (`chat_prompt_history.js`), attachments (`chat_attachments.js`),
  action forms (`chat_action_forms.js`), agent badges/slash
  (`chat_agent_model.js`, `chat_slash.js`), activity
  (`chat_activity.js`), projects (`chat_project.js`), composer
  decisions (`chat_composer.js`).
- Search: split by feature, no new extraction. In-chat find decisions
  are owned by `chat_find.js` (`CuttleChatFindApi`: electron detect,
  key actions, match offsets, step index). History-panel search stays
  page orchestration (`historySearchLiveQuery` state + paint gates in
  `loadChatHistory`/panel paths) because the query state gates live
  server/local session paint, not a pure match — gate tests
  (`test_chat_history_search.py`, incl. the hard full-list-paint gate)
  are the contract; navigability via those tests.
- Panel/delete/rename/persistence + DOM predicates stay page
  orchestration: `paintHistoryEntries`/`createHistoryItemHTML`/
  `createAuthHistoryItemHTML`, `deleteChatSession` (16383),
  `renameChatSession` (16943), `isChatSessionDeleted`,
  `closeHistoryItemMenu` — they compose auth state, localStorage, live
  DOM, and server sessions per paint; extracting them would strand
  paint order in callback bags. Contracts: history-delete-modal,
  pagination, and search gate tests. Feature navigability: panel paint
  flows through `loadChatHistory` → `paintHistoryEntries`.
- Assigned to Slice 9 ONLY on streaming/lifecycle evidence (not
  size): `loadChatHistory` (17331) consumes live server sessions
  (`CuttleAuth.getSessions`), generation flags
  (`applyGeneratingFlagsFromSessions`), and shares paint gates with
  the poll timers (`messageSyncTimer` 13496,
  `historyGeneratingPollTimer` 12053); transcript poll/append,
  session restore ordering (prefs/render/live-status), and
  `migrateLocalChatSession` (runs inside session-remap lifecycle).
  Required Slice 9 acceptance: (1) preserve search-gate semantics
  (no full-list paint over an active query); (2) preserve
  last-message-time sort stability; (3) preserve
  prefs/render/live-status restore order with existing
  execution-level tests green; (4) keep prompt-history session
  migration (`migratePromptHistorySession` + `loadPromptHistory-
  ForSession`) wired to remap events. No other Messages/History
  logic is known-unowned: domain work is complete except the Slice 9
  lifecycle items above. Streaming itself is NOT started here.
- Risks/limits: null-transcript hardening is new-tolerant by design
  (documented); browser/Flask manual validation unavailable
  (node/fake-DOM only); `migrateLocalChatSession` merge stays inline
  (lifecycle-adjacent, Slice 9).
- Commit independently on main. No push, no restart.
  **STOP for Codex review before Slice 9/Streaming.**

## Prior approval recorded: Slice 8F f2df4ae4 + Messages/History closure

- External review approves 8F and the Messages/History closure with the
  explicit Slice 9 lifecycle carry-forward contracts. Reviewer last read
  line 4558; this section appends only beyond that boundary; earlier
  sections preserved.

## Slice 9A report — turn/staleness guard ownership (first portion)

- Inventory drove the subdivision: generation state (`isLoading`,
  `localGeneratingSessionId`, `runningSessionIds`,
  `activeRequestController`, `activeEventSource`,
  `userStoppedGeneration` + suppress flags), follow-up queue
  (`pendingFollowups` + drain/take/reconcile/render), stop/cancel
  (`stopGenerating`, detach, shell-pause release), pending-result
  waiter (`collectPendingResult` + poll timers), SSE arms, and
  poll/busy machinery form one coupled lifecycle; moving any whole
  would be the giant-closure move the slice forbids. 9A takes the
  narrowest cross-cutting seam: the staleness token every one of those
  bodies already checks.
- New owner `src/web/js/chat_turn_guard.js` (`CuttleTurnGuard`, IIFE +
  `module.exports`, zero DOM/fetch/timers): `createGeneration` (page
  holds exactly one instance), `bump` (navigation invalidates all
  prior turns), `capture` (send-time snapshot, also ferried as
  `opts.navGen`), `isStale` (null-token-never-stale preserves the
  waiter's `turnNavGen != null &&` shape), `canPaintHere`
  (freshness-then-binding truth table; session lookup lazy via an
  `isViewing` callback). Bodies keep all control flow; only the
  comparisons move.
- Page rewire (signatures and order preserved): token source, detach
  bump, send-time capture, four waiter checks → `isStale`, adopt gate
  → `!isStale`, `navAway` → `isStale`; `canPaintTurnHere` stays as a
  5-line adapter binding the mutable per-turn bound id + live session
  state (8 call sites untouched). No other `chatNavGeneration` readers
  or writers existed (single bump site verified).
- Coverage (committed, real module execution): new
  `test_chat_turn_guard.py` (5 tests: source/bump/null-safety incl.
  double bump, full paint truth table, New-Chat-during-turn lifecycle
  — fresh paints, zombie blocked, waiter exits, new turn paints, old
  stays stale — tag pin). Pre-move differential `/tmp/diff_turn.js`
  (scratch): real page closure + check expressions vs module over
  gens×captured×bound×current (incl. null/undefined tokens) plus
  lifecycle sequences — IDENTICAL throughout. Two
  `test_chat_cross_session_activity.py` pins updated to the new call
  shapes (same contracts: token source in detach, zombie adopt gate);
  SSE-status/pending-poll gating pins pass unchanged via the kept
  adapter.
- Gates, same controlled scope (`--ignore=src/tests/unit`, spend flags
  verified unset, zero `SPENDING REAL TOKENS` trips): focused
  lifecycle 38 passed + 1 known pre-existing failure
  (`test_cursor_stream_switch_does_not_chirp_mid_run` — stale pin on
  unrelated `processCursorCommandStreaming`, fails identically on
  stashed 8F HEAD); neighbors 256 passed with only the 6 accounted
  pre-existing failures (composer duplicate, steer, starred-removal
  green); broad **28 failed / 1844 passed / 79 skipped** with FAILED
  identities byte-identical to the 8F 28 (`diff` clean; +5 passed = 5
  new tests). `node --check` clean on all touched JS.
- Cache: `chat_page.html` adds versioned
  `chat_turn_guard.js?v=20261001slice9a` (after prompt-history,
  before page) and bumps `chat_page.js` to `slice9a`; all other
  assets untouched with matching fingerprints.
- Files: `src/web/js/chat_turn_guard.js` (new),
  `src/web/js/chat_page.js` (token source/checks/closure/comments),
  `src/web/chat_page.html` (+2/−1 tags),
  `src/tests/test_chat_turn_guard.py` (new),
  `src/tests/test_chat_cross_session_activity.py` (2 pin updates),
  this section. No backend/routing/product-behavior changes.

## Remaining Slice 9 portions + Phase 3 closure acceptance

- 9B (candidate): stop/cancel state transitions — `userStoppedGeneration`
  + `suppressStreamAbortUi`/`suppressRemoteWaitingAfterStop` + transport
  teardown across `stopGenerating`/detach/shell-pause release; keep
  stop→send races and instant-notice semantics pinned first.
- 9C (candidate): follow-up queue — `pendingFollowups` + drain timer +
  take/reconcile + pause/resume render; combine-on-idle and Stop-liter
  semantics are the contracts to preserve.
- 9D (candidate): pending-result waiter + replay/reconcile/dedup +
  message-sync poll timers + busy lock/heal; exactly-once
  append/persist and disconnect/reconnect behavior pinned first.
- Phase 3 closure acceptance (carried, unchanged): search gate over
  active query; last-message-time sort stability; prefs/render/
  live-status restore order; prompt-history migration wired to remap;
  `loadChatHistory`/poll generation-flag interfaces explicit; no
  paid-prompt tests run; manual browser/Flask validation reported as
  unavailable (node/fake-DOM only).
- Risks/limits: guard adapter keeps 8 bound call sites in the page by
  design (mutable per-turn state); no other turn-token readers exist,
  verified by search.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before 9B or Phase 4.**

## Prior approval recorded: Slice 9A 321f5c9b

- External review approves 9A. Reviewer last read line 4650; this
  section appends only beyond that boundary; earlier sections preserved.

## Slice 9B report — stop/cancel lifecycle ownership

- New owner `src/web/js/chat_stop_state.js` (`CuttleStopState`, IIFE +
  `module.exports`, zero DOM/fetch/timers/transports): the three
  stop flags as ONE state object `{ userStopped, abortSuppressed,
  waitingSuppressed }` plus the transition table — `requestStop`
  (terminal, idempotent), `beginSend` (clears stop latches, keeps
  orphan suppression), `markStreamDetached` (navigation/shell-pause/
  server-sync finish), `clearAbortSuppression` (generation begin +
  explicit-stop recovery), `clearWaitingSuppression` (new chat /
  switch), and `classifySendAbort` (`detached` keeps suppression,
  `stopped` clears it, `dropped`/`other` leave it). Turn-guard tokens
  stay owned by `chat_turn_guard.js`; follow-up queue timer state
  stays in the page (9C); backend cancel transport
  (`requestServerCancelCurrentRun`) and local teardown try/catch
  lines stay page-side (divergent null semantics kept verbatim).
- Page rewire: 3 `let`s → 1 `stopState` instance; all writers →
  transitions (stop, send, detach, shell-pause, server-sync finish,
  generation begin, new-chat/switch ×2, send-catch classify); all 29
  readers → `stopState.*` fields. No split state, no other writers
  (verified by search).
- Coverage (committed, executed BEFORE moves where it matters): new
  `test_chat_stop_state.py` (7 tests). Module truth table + race
  sequences + abort-classification matrix ran green pre-rewire. The
  adapter suite executes the REAL page `stopGenerating` / detach /
  shell-pause / begin / server-sync-finish bodies with fake
  AbortController/EventSource/fetch/DOM/timers over idle, live,
  repeat, throwing-transport, detach, pause/teardown, begin, and
  finish-idle/live scenarios — green pre-rewire (pinning old-code
  effects, incl. the throwing-close-skips-its-null asymmetry) and
  green post-rewire with identical assertions: the pre/post
  differential. Three pins repointed to the new call shapes
  (cross-session detach, followup-heal reader, pause-not-stop);
  stop-refresh prose mentions need no change.
- Gates, same controlled scope (`--ignore=src/tests/unit`, spend flags
  verified unset, zero `SPENDING REAL TOKENS` trips): neighbors 279
  passed with only the 7 accounted pre-existing failures (composer
  duplicate, steer, starred-removal, turn-guard, cross-session gating
  green); broad **28 failed / 1851 passed / 79 skipped** with FAILED
  identities byte-identical to the 8F/9A 28 (`diff` clean; +7 passed
  = 7 new tests). `node --check` clean on all touched JS.
- Cache: `chat_page.html` adds versioned
  `chat_stop_state.js?v=20261001slice9b` (after turn-guard, before
  page) and bumps `chat_page.js` to `slice9b`; all other assets
  untouched with matching fingerprints.
- Files: `src/web/js/chat_stop_state.js` (new),
  `src/web/js/chat_page.js` (state object + transitions + reader
  renames), `src/web/chat_page.html` (+2/−1 tags),
  `src/tests/test_chat_stop_state.py` (new),
  `src/tests/test_chat_cross_session_activity.py`,
  `src/tests/test_chat_followup_heal.py`,
  `src/tests/test_chat_pause_not_stop.py` (pin repoints), this
  section. No backend/routing/product-behavior changes.

## Remaining Slice 9 + Phase 3 closure acceptance (unchanged)

- 9C: follow-up queue (`pendingFollowups` + drain timer +
  take/reconcile + pause/resume render; combine-on-idle and Stop-liter
  semantics preserved). 9D: pending-result waiter + replay/reconcile/
  dedup + message-sync poll timers + busy lock/heal; exactly-once
  append/persist and disconnect/reconnect pinned first.
- Closure acceptance carried: search gate over active query;
  last-message-time sort stability; prefs/render/live-status restore
  order; prompt-history migration wired to remap; loadChatHistory/poll
  generation-flag interfaces explicit; no paid-prompt tests run;
  manual browser/Flask validation unavailable (node/fake-DOM only).
- Risks/limits: teardown null-semantics asymmetry (throwing close
  skips its null) pinned as-is, not normalized; send-catch
  classification verified branch-identical by construction + adapter
  effects, not by executing the full send flow (too DOM-deep for the
  span harness — noted honestly).
- Commit independently on main. No push, no restart.
  **STOP for Codex review before 9C or Phase 4.**

## Prior approval recorded: Slice 9B 7fa788a2

- External review approves 9B, carrying the full send-catch
  integration limitation into later lifecycle checks. Reviewer last
  read line 4729; this section appends only beyond that boundary;
  earlier sections preserved.

## Slice 9C report — follow-up queue ownership

- New owner `src/web/js/chat_followup_queue.js`
  (`CuttleFollowupQueue`, IIFE + `module.exports`, zero DOM/fetch/
  timers): the queue as ONE state object `{ items, dirty,
  takeInFlight }` plus transitions (`enqueue` with injected clock,
  `setPaused`, `removeItem`, `clearAll`, dirty/take latches) and the
  two pure decisions — `resolveTake` (server-take / fallback /
  empty-queue-noop branches incl. server-remaining-as-is verbatim)
  and `reconcileServerList` (busy/editing/invalid/same guards +
  normalize). Item shaping (fingerprint, normalize, partition,
  combine) stays owned by `chat_activity.js` and is used ONLY
  through an explicit `activity` dependency — composed, never
  duplicated. Drain-timer handle, edit UI state, persistence
  transport, render, heal/send effects stay page-side.
- Page rewire: 4 `let`s → 1 `followupQueue` instance; enqueue,
  pause/resume, remove, clear, reconcile, persist ×2, and the full
  drain take-branch → owned calls; edit-UI paused flips route via
  `setPaused`; remaining readers → `followupQueue.items`. Steer-first
  path untouched (steer → queue fallback order preserved).
- Coverage (committed, executed BEFORE moves): new
  `test_chat_followup_queue.py` (7 tests). Module truth table +
  take/reconcile branches green pre-rewire. The adapter suite
  executes the REAL page enqueue/schedule/drain/reconcile/persist
  bodies with fake timers, scripted fetch, and DOM stubs over
  enqueue-reconcile, schedule gates, server-take (with/without
  remaining), take-failure/local/no-sid fallbacks, mid-PUT edit race,
  pause/resume, remove/clear, and all six reconcile guards — green
  pre-rewire (pinning old-code effects, incl. POST-echo
  reconciliation scheduling drains) and green post-rewire with
  identical assertions: the pre/post differential. The 9B stop
  adapter and the Slice 7 composer sendMessage harnesses were
  migrated to the owned state object (duplicate-send contracts
  re-verified through real send + real queue module).
- Gates, same controlled scope (`--ignore=src/tests/unit`, spend flags
  verified unset, zero `SPENDING REAL TOKENS` trips): neighbors 286
  passed with only the 7 accounted pre-existing failures (composer
  duplicate, steer, turn-guard, cross-session gating green); broad
  **28 failed / 1858 passed / 79 skipped** with FAILED identities
  byte-identical to the 9B 28 (`diff` clean; +7 passed = 7 new
  tests). `node --check` clean on all touched JS.
- Cache: `chat_page.html` adds versioned
  `chat_followup_queue.js?v=20261001slice9c` (after stop-state,
  before page) and bumps `chat_page.js` to `slice9c`; all other
  assets untouched with matching fingerprints.
- Files: `src/web/js/chat_followup_queue.js` (new),
  `src/web/js/chat_page.js` (queue state + decisions + readers),
  `src/web/chat_page.html` (+2/−1 tags),
  `src/tests/test_chat_followup_queue.py` (new),
  `src/tests/test_chat_stop_state.py`,
  `src/tests/test_chat_composer.py` (harness state migration), this
  section. No backend/routing/product-behavior changes.

## Remaining Slice 9 + Phase 3 closure acceptance (unchanged)

- 9D: pending-result waiter (`collectPendingResult`) + replay/
  reconcile/dedup + message-sync poll timers + busy lock/heal;
  exactly-once append/persist and disconnect/reconnect pinned first.
  SSE delivery arms stay with their bodies; backend turn
  orchestration reserved for Phases 4/5.
- Closure acceptance carried: search gate over active query;
  last-message-time sort stability; prefs/render/live-status restore
  order; prompt-history migration wired to remap; loadChatHistory/poll
  generation-flag interfaces explicit; full send-catch integration
  limitation carried from 9B; no paid-prompt tests run; manual
  browser/Flask validation unavailable (node/fake-DOM only).
- Risks/limits: edit-UI content edits write item fields directly
  (edit-domain, documented); timer handle deliberately stays
  page-side as the scheduler effect.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before 9D or Phase 4.**

## Prior approval recorded: 9C 5f0fa257

- Codex review **APPROVED** Slice 9C (follow-up queue ownership) at
  `5f0fa257`. No production changes requested. Next: Slice 9D
  pending-result/replay/reconcile/dedup/poll/busy-heal lifecycle only;
  stop before any further portion or Phase 4. No push/restart.

## Slice 9D report — pending-result / replay / reconcile lifecycle
  (`chat_pending_result.js`, closes carried 9B send-catch gap at decision
  level)

- Owner: `src/web/js/chat_pending_result.js` (`CuttleChatPendingResult`,
  loaded after turn-guard + stop-state, before page). Owns result-lifecycle
  decisions + injected-effect async orchestration with explicit narrow
  interfaces; no document/window/fetch/timers. Page keeps timers
  (`messageSyncTimer`, `historyGeneratingPollTimer`), transport URLs,
  DOM/persistence, busy-lock set/clear (`begin/endLocalGeneration`),
  session-adopt nav guards, and applies every effect.
- Boundary (each verified below):
  `classifyStreamEvent` (pure SSE status/session/busy/query/response —
  busy bubble text preserved exactly); `waitForPendingResult` (waiter loop:
  stale-nav/stop/peek-skip/transient-deadline/consume-on-hit/3rd-poll
  history/deadline-miss); `findRecoverableAssistant` (pure match) +
  `recoverChatResult`/`WithRetries` + `consumeParkedChatResult`;
  `classifyServerMessage` (12 exactly-once sync decisions incl.
  control_request_id claim, skip-cancelled/stopped, claim/adopt/append,
  dup-user/assistant stamping); `decideStaleHeal`
  (finish-orphan/zombie, clear-remote+drain, none); send-catch sequence
  `recoverAfterStreamDetach` (detach tail incl. painted-shortcut, nav-away
  guard, live second round), `recoverAfterTransportFailure` (restart poll +
  exactly one waiter/history attempt, then live handoff or rethrow —
  deliberately NOT the detach tail: no painted-shortcut, no second
  90-min round), `recoverControlLaneFailure` (history-only, no POST
  retry); `waitForServerRecovery` (restart poll, page-held in-flight
  guard + notified ids).
- Turn/stop/queue composed through owned interfaces only
  (`CuttleTurnGuard`, `CuttleStopState`, queue drain callback) — never
  reimplemented or duplicated. Message/record helpers
  (`normalizeMessageContentForMatch`, `normalizeUsagePayload`,
  `isCancelledAgentText`, `uiAlreadyHasMessage`, claim/stamp fns) used as
  injected deps. Session-adopt guards stay page-side (nav state).
- Diffs: page bodies (`collectPendingResult`,
  `recoverChatResultFromServer/WithRetries`, `consumeParkedChatResult`,
  `recoverAfterFlaskRestart`, heal, sync per-message chain, SSE fold,
  detach/control/transport catch blocks) become thin adapters with
  identical call sites, flag accumulation, strings, and ordering. Two
  near-miss drifts caught during authoring and corrected before commit:
  transport path briefly shared the detach tail (would have added a
  painted-shortcut + second 90-min wait before throw); painted-shortcut
  first used raw-string presence (empty-raw reply would have been
  skipped) — contract is now `{ present, raw }`.
- Coverage (committed `src/tests/test_chat_pending_result.py`, 12 tests,
  node-executed real module with fake fetch/clock, all green):
  ORACLE differentials captured by executing pre-change page functions
  with equivalent fakes BEFORE the move (scratch `/tmp/pre9d_oracle.js`,
  out of tree) — waiter hit shape + consume count, stale/no-fetch,
  stopped, peek-skip + debug name, transient-`!success` deadline-null
  with history never consulted, idle miss, match/trailing/painted/
  cancelled recovery, orphan-finish/live-none/stopped-none/
  remote-clear+drain/idle-none heal — embedded as literals, all equal
  post-move. Post-move integration (scratch `/tmp/post9d_adapter.js`):
  real page adapters + real module reproduce the oracle values.
  Discrimination: duplicate→append mutation fails
  `test_sync_classification`; dropped-consume mutation fails
  `test_waiter_hit_consume_and_shape`; both restored byte-identical
  (`cmp` clean). Structural pin updates required by the intended move
  (behavior preserved, previously green on HEAD): 3 heal/wait/sync pins
  in `test_chat_followup_heal.py`, nav-away pin in
  `test_chat_false_reply_ready.py`, `_sse_event_arms` in
  `test_chat_cross_session_activity.py` (now asserts
  `streamEv.kind` arms + owned classification). 9B send-catch gap:
  closed at decision level — catch blocks now route through the owner
  with fake-testable deps (detach/transport/control/handoff/throw all
  executed); byte-level SSE transport loop remains page orchestration.
- Gates (same env/invocation/scope throughout: main-repo `.venv`,
  node shim on `PATH`, `pytest -q -p no:warnings src/tests/
  --ignore=src/tests/unit`, unit exclusion still the proven
  `test_security.py` collection error): focused+neighbors green;
  broad **28 failed / 1870 passed / 79 skipped** with sorted FAILED
  identities `diff`-clean against the same-tree HEAD baseline
  (`git stash -u` round-trip, 28 failed / 1858 passed / 79 skipped;
  +12 passed = 12 new tests, −0/+0 failures). A detached-worktree
  baseline attempt showed 39 failures (untracked `src/.env` and
  machine state do not carry into worktrees) and was discarded in favor
  of the same-tree stash comparison. `node --check` clean on all
  touched JS.
- Cache: `chat_page.html` adds versioned
  `chat_pending_result.js?v=20261001slice9d` (after followup-queue,
  before page) and bumps `chat_page.js` to `slice9d`; no other assets
  changed.
- Spend audit: `CUTTLE_AGENT_SMOKE`/`CUTTLE_ALLOW_SPEND` unset (verified
  empty in this shell); no paid/token prompt tests run; paid suites
  self-skip per `spend_guard.py`.
- Manual validation unavailable (node/fake-DOM only — no browser/Flask);
  no visible UI changes. Untouched by design: search gate over active
  query, last-message-time sort, prefs/render/live-status restore
  order, prompt-history remap, `loadChatHistory`/poll generation-flag
  interfaces, backend routing/lifecycle (Phase 4/5).
- Files: `src/web/js/chat_pending_result.js` (new),
  `src/web/js/chat_page.js` (adapters only),
  `src/web/chat_page.html` (+1 tag, +2 version bumps),
  `src/tests/test_chat_pending_result.py` (new),
  `src/tests/test_chat_followup_heal.py`,
  `src/tests/test_chat_false_reply_ready.py`,
  `src/tests/test_chat_cross_session_activity.py` (pin updates), this
  section. No backend/routing/product-behavior changes.
- Remaining 9E/closure question for review: message-sync + history poll
  *timers* and the busy-lock primitives remain page-side schedulers by
  design; SSE byte-transport loop remains page orchestration. If the
  reviewer wants those under explicit owners, that is the exact 9E
  boundary (timer scheduler owner + busy-lock owner) — otherwise Phase 3
  page-orchestration remainder is declared and Phase 4 may proceed.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before 9E/Phase 4.**

## Prior approval recorded: 9D eff783fe

- Codex review **APPROVED** Slice 9D (pending-result/replay/reconcile
  lifecycle, `chat_pending_result.js`) at `eff783fe`. No production
  changes requested. Next: final Phase 3 checkpoint 9E (busy-generation
  state/poll scheduler ownership + closure verification); no Phase 4
  implementation yet. No push/restart.

## Slice 9E report — busy-generation lifecycle + Phase 3 closure
  (`chat_generation.js`)

- Owner: `src/web/js/chat_generation.js` (`CuttleChatGeneration`,
  loaded after pending-result, before page). ONE explicit busy-lock
  state object `{ loading, localSessionId, seq }` + transitions; sync
  cadence decision; detached-poll classification; session-open flag
  decision. No document/window/fetch/timers; no reverse dependencies
  (verified: only its own export references `Cuttle*`). Page holds the
  single instance and performs ALL effects: timer handles/scheduling,
  transport, DOM, voice phase, stop transitions, running-flag paint.
- Boundary: `beginGeneration` (idempotent re-begin keeps the token —
  composer + processMessage double-begin preserved);
  `endGeneration(state, token)` — token mismatch never releases
  (fixes the Stop→send race where a stale turn callback could clear a
  newer turn's lock; force with no token reserved for Stop/chat-delete,
  which own the turn); `detachGeneration` (always wins, invalidates
  pre-detach tokens, keeps spinner id); `rebindGeneration` (session
  rebind while loading only); `isLoadingForSession` (viewing-gated
  predicate, injected); `decideSyncDelayMs` + `SYNC_MS` (all six cadence
  branches + inflightMax, verbatim values); `classifyDetachedPoll`
  (stop/notify-done/keep-waiting/spin-down, fetch order preserved: live
  status fetched only when no parked body and not generating);
  `decideSessionOpen` (default-idle-until-live-status on switch).
  Turn/stop/queue/pending-result composed via interfaces only.
- Token threading: `beginLocalGeneration()` returns the token;
  processMessage captures `turnGenToken` and all its end calls
  (handoff, silent-form, control-skip, finally) pass it;
  `finishLocalStreamFromServerSync` passes the current token; Stop and
  chat-delete force. `isLoading`/`localGeneratingSessionId` readers
  renamed mechanically to `generation.loading`/`generation.localSessionId`
  (24 code sites; strings/comments untouched; `isLoadingThisSession`
  keeps its name as a thin adapter).
- Diffs: transition bodies become adapters; page diff is +24 net
  (adapter comments/descriptors/token threading outweigh removed
  bodies) — ownership, not line count, is the metric. No behavior
  change except the specified stale-release hardening.
- Coverage (committed `src/tests/test_chat_generation.py`, 10 tests,
  all green): ORACLE differentials from executing pre-change page
  functions BEFORE the move (scratch `/tmp/pre9e_oracle.js`) — begin
  claim + idempotent re-begin, request-id fallback, end release,
  detach (bump/suppress/keep-spinner/watcher), all six cadences,
  schedule-replace, start no-double/gating, stop cancel, viewing
  gates — embedded as literals, all equal post-move. Post-move
  integration (scratch `/tmp/post9e_adapter.js`): real page adapters +
  real module reproduce oracle values, plus stale-token no-release.
  Discrimination: always-release mutation fails
  `test_stale_token_never_releases_newer_turn`; cadence-swap mutation
  fails `test_sync_cadence_branches`; restored byte-identical.
  Structural pin updates required by the move: 9B stop-state adapter
  harness migrated to the owned lock (now true page+module
  integration), interval pin retargeted to `SYNC_MS` + delegation.
- Closure contracts (all preserved, no moves): active-query search
  paint gate (`historySearchQueryActive` gates untouched);
  last-message-time stable sort; prefs/render/live-status restore order
  (`restoreSessionStickySlash` prefs-first, transcript inference after
  `/messages`); prompt-history remap migration owned by
  `chat_prompt_history.js`; generation-flag interfaces
  (`setHistorySessionRunning` call sites unchanged);
  duplicate append/persist protection (`seenControlRequestIds`,
  control_request_id claim); reconnect/parked/stop/steer/cross-session
  paths; composer/markdown/render contracts — covered by the broad gate.
- Gates (same env/invocation/scope: main-repo `.venv`, node shim on
  `PATH`, `pytest -q -p no:warnings src/tests/
  --ignore=src/tests/unit`): focused/neighbors green (only the known
  pre-existing `test_cursor_stream_switch_does_not_chirp_mid_run`
  fails — fails on HEAD too); broad **28 failed / 1880 passed /
  79 skipped** with sorted FAILED identities `diff`-clean against the
  9D HEAD baseline (28/1870/79 via `git stash -u` round-trip; +10
  passed = 10 new tests, −0/+0 failures). `node --check` clean.
- Cache: `chat_page.html` adds versioned
  `chat_generation.js?v=20261001slice9e` and bumps `chat_page.js` to
  `slice9e`; no other assets changed.
- Spend audit: `CUTTLE_AGENT_SMOKE`/`CUTTLE_ALLOW_SPEND` unset (verified
  zero in this shell); no paid/token prompt tests run.
- Manual validation unavailable (node/fake-DOM only — no browser/Flask);
  no visible UI changes.

## Phase 3 closure summary

- Planned domains and owners: turn/staleness → `chat_turn_guard.js`;
  stop/cancel → `chat_stop_state.js`; follow-up queue →
  `chat_followup_queue.js` (+ `chat_activity.js` items);
  pending-result/replay/reconcile/SSE classes → `chat_pending_result.js`;
  busy-generation/poll cadence → `chat_generation.js`;
  messages/history (8A–8F) → `chat_messages.js`,
  `chat_activate.js`, `chat_markdown.js`, `chat_prompt_history.js`.
  Navigation map updated in `docs/architecture/repository-map.md`
  (Frontend ownership table).
- Metrics: `chat_page.js` 24265 → 24289 lines across 9E (+24 net —
  mechanical reader rename + comments/descriptors; lifecycle decision
  bodies now live in the 179-line owner); the five lifecycle owners
  total 1126 lines with narrow interfaces; before/after behavior proven by
  oracle differentials + `diff`-clean broad gates per slice, not by
  line counts.
- Remaining page-side by design (with justification): message-sync and
  history timer handles/mechanics (dumb schedulers — decisions owned);
  SSE byte-transport loop (transport, event classes owned);
  session-adopt nav guards (navigation state); running-flag store/paint
  (server-truth fan-in from many paths — splitting it would fragment);
  render orchestration and persistence effects (orchestrator role).
- Reverse-dependency checks: each owner module is standalone (no
  `document`/`window`/`fetch`/timers/`Cuttle*` references); page
  depends on owners, never the reverse.
- Proposed Phase 4 slices (inventory only, no implementation):
  P4-1 backend turn orchestration (`process_message_with_bot` dispatch
  vs `chat_delivery`/`chat_run_registry` seams); P4-2 SSE/delivery
  transport ownership (server event arms vs client waiter contract);
  P4-3 router/harness selection boundaries (`agent_router` brain vs
  sticky/starred executors). Each needs Codex dispatch.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before Phase 4.**

## Prior approval recorded: 9E 415bf3a7 and Phase 3 closure

- Codex review **APPROVED** Slice 9E (`chat_generation.js`, token-scoped
  stale-release race fix — explicitly intentional and tested, the one
  exception to behavior preservation) and the Phase 3 closure. Next:
  Phase 4 Shared Chat Lifecycle Backend P4-1 only; no Phase 5/6
  changes. No push/restart.

## Phase 4 baseline — dependency / lifetime / reverse-import map

- Scope correction applied: Phase 4 prepares owned interfaces/services
  and reduces reverse imports; it is NOT turn-coordinator extraction
  (Phase 5) nor router/harness redesign (Phase 6). Runner functions
  (`_run_*_web_command`), the coordinator workflow, and router brains
  stay untouched.
- Production reverse imports into `web_chat_api.py` before P4-1: 15
  `from` sites (method: `grep -rn "from api.web_chat_api import\|from
  api import web_chat_api" src/ --include="*.py" excluding tests,
  before/after file lists diffed). Of those, 6 are live-status store
  users: `chat_delivery` (get/clear), `chat_run_registry` (clear),
  `auth_api` (active ids ×2), supervised `orchestrator` (set). The
  rest are out of P4-1 scope: harness runner fns (`adapters` ×2,
  `dispatch` — Phase 5), `internal_http` test-client transport,
  `_default_chat_cwd` / badge/metadata helpers (later P4 slices),
  status-queue emit, `doctor` noqa.
- Current state owners/lifetimes: live-status dict — process lifetime,
  in-memory, wiped on Flask restart (chats default idle until
  live-status confirms; detached watchers re-poll); SQLite auth/chat
  history — persistent; delivery busy/cancel sticky — process lifetime
  (`chat_delivery` module state); run registry — process lifetime with
  SQLite task rows (daemon-owned workers); session/project resolution —
  per-request; restart status file — daemon-owned. Critical rule:
  restart-sensitive process state must never move into transient
  per-request objects.
- First boundary (P4-1): live-status store service — lowest risk (pure
  store + TTL, single writer semantics already lock-guarded), removes
  6 reverse imports including the hidden `chat_delivery ↔ web_chat_api`
  cycle (delivery imported live-status from the monolith while the
  monolith imports delivery's cancel/busy predicates).

## P4-1 report — `api.chat_live_status` service

- New `src/api/chat_live_status.py`: owns `_STORE` + `_LOCK` (process
  lifetime, same semantics), `TTL_SECONDS` (45 min) +
  `CONNECTING_TTL_SECONDS` (90 s), key-variant mapping (via leaf
  `api.session_keys`, no cycle), `set/get/clear_live_status` +
  `active_live_session_ids` moved verbatim. Cancel predicate is an
  injected per-call dep (`is_cancelled`) — the service imports nothing
  but `session_keys`/`threading`/`time` (verified by test). No routes,
  no persistence writes, no orchestration.
- `web_chat_api.py` keeps thin wrappers under the existing names (all
  ~20 internal call sites + tests untouched) and injects
  `chat_delivery.is_turn_cancelled` at the composition root; keeps
  `_chat_live_status`/`_lock`/`_keys`/TTL names as aliases to the
  single shared store (never a replica). Routes
  (`/api/chat-live-status`), `_public_live_generating` composition, and
  `_chat_status_queues` streaming queues stay put (routes/composition
  are not extracted for size).
- Consumers rewired to the service: `chat_delivery` (2),
  `chat_run_registry` (1), `auth_api` (2), supervised `orchestrator`
  (1). One test-target update required by the move:
  `test_supervised_coordinator.py` monkeypatch now targets
  `api.chat_live_status.set_live_status` (same call-time import
  pattern, still effective).
- Reverse imports: 15 → 9 (all 6 live-status sites gone; remaining 9
  listed in the baseline above for later P4 slices).
- Coverage (committed `src/tests/test_chat_live_status_service.py`,
  13 tests, all green): 8 behavior tests written and run BEFORE the
  move (roundtrip/key variants, field preservation, TTL + Connecting
  fast-path eviction, clear, active ids, Stop cancel guard, unknown
  idle) + 5 post-move tests (no-monolith-import source guard, store
  identity across service/wrapper both directions, module-cache single
  instance, orchestrator inactive-row integration, wrapper-guard vs
  service-default). Lifetime proof: `wca._chat_live_status is
  svc._STORE` and shared lock — the original instance, not a replica.
- Gates (same env/invocation/scope: `.venv`, `pytest -q -p no:warnings
  src/tests/ --ignore=src/tests/unit`, unit exclusion still the proven
  `test_security.py` collection error): focused + lifecycle neighbors
  (steer/stop/followup/restart/recovery/supervised) green; broad **28
  failed / 1893 passed / 79 skipped** with sorted FAILED identities
  `diff`-clean against the 9E baseline (28/1880/79; +13 = new tests,
  −0/+0 failures).
- Spend audit: `CUTTLE_AGENT_SMOKE`/`CUTTLE_ALLOW_SPEND` unset; no
  paid/token prompt tests run (fake executors not needed — no
  executor paths touched; no new prompt-sending tests added).
- Manual validation N/A (backend service; no UI change). No Flask
  restart/kill performed.
- Files: `src/api/chat_live_status.py` (new),
  `src/api/web_chat_api.py` (wrappers + aliases),
  `src/api/chat_delivery.py`, `src/api/chat_run_registry.py`,
  `src/api/auth_api.py`,
  `src/api/agent_router/supervised/orchestrator.py` (consumer rewires),
  `src/tests/test_chat_live_status_service.py` (new),
  `src/tests/test_supervised_coordinator.py` (monkeypatch target),
  this section + `docs/architecture/repository-map.md` (service row).
- Remaining Phase 4 slices + completion criteria: P4-2 status-queue +
  emit service (`_chat_status_queues`, `emit_chat_status` consumers);
  P4-3 small pure helpers (`_default_chat_cwd`, badge/metadata
  fns — verify no hidden state first); runner fns and coordinator stay
  for Phase 5, router brains for Phase 6. Phase 4 done when production
  reverse imports into `web_chat_api.py` are only the Phase-5-owned
  runner calls + `internal_http` transport (counted by the same grep
  method, target ≤ 4 with justification).
- Commit independently on main. No push, no restart.
  **STOP for Codex review before P4-2/Phase 5.**

## Prior approval recorded: P4-1 bff30cc6

- Codex review **APPROVED** P4-1 (`api.chat_live_status` service) at
  `bff30cc6`. No production changes requested. Next: P4-2 status
  queue/emit service only; no coordinator extraction, no Phase 5/6.
  No push/restart.

## P4-2 report — `api.chat_status` status-queue/emit service

- New `src/api/chat_status.py`: owns the process-lifetime
  session→transport-queue registry (`_QUEUES`; register replaces,
  unregister pops, lookup returns None when detached) plus the emit
  fanout (cancel check → live publish → `put_nowait(('status', msg))`
  with `Full` dropped), moved verbatim. Queue objects are created and
  drained by the transport/route layer; the service never touches
  Flask routes, SSE serialization, delivery orchestration, or
  persistence. Imports nothing but `queue`/`typing` (verified by
  test) — no entry-module, delivery, or duplicate-cache dependency.
- `emit_status` requires explicit `is_cancelled` + `publish_live`
  keyword args (no defaults — enforced by signature test), so no
  consumer can silently call an unguarded default for active writes.
  `web_chat_api.emit_chat_status` is a thin wrapper injecting
  `_chat_turn_cancelled` and the live-status publish; all ~20 producer
  call sites (turn workflow, Phase 5 territory) are byte-untouched.
  Registration/unregistration in `process_message_with_bot` delegate
  (`finally` semantics preserved: pop exactly when a local queue was
  passed). Queue creation, the `('done', result)` completion put, and
  the SSE `pump_status` loop (stale/cancel narration guards, live
  publish on status events, done handling) stay in transport/route
  code — not extracted for size.
- `chat_status_phases.emit_pipeline_status` now takes an injected
  `emit_fn` (single production caller passes `emit_chat_status`);
  the leaf no longer reverse-imports the entry module. P4-1 writer is
  inactive (orchestrator publishes `active=False`); verified no new
  active writers: every `emit_status`/`emit_chat_status` call path
  carries the turn-cancelled predicate (wrapper or explicit arg).
- Reverse imports: 9 → 8 (the phases reverse import is gone;
  remaining: harness `kernel`, router `dispatch` + `adapters` ×2,
  `doctor` noqa, `internal_http` transport, subagent
  `identity`/`turns` helpers — same grep method, before/after file
  lists diffed).
- Coverage (committed `src/tests/test_chat_status_service.py`, 17
  tests, all green): 10 behavior tests written and run BEFORE the
  move (register/lookup/unregister, fanout to queue + live, FIFO,
  no-queue live-only, full-queue drop, cancel suppresses both arms,
  emit-after-unregister, overwrite replaces, concurrent
  emit/unregister consistency, phase formatting via wrapper path) + 7
  post-move tests (injected phase formatting, phase leaf import
  guard, phases-through-guarded-wrapper, service import guard,
  registry identity both directions, module-cache single instance,
  end-to-end emit with fakes incl. raising publisher, explicit-policy
  signature). Lifetime proof: `wca._chat_status_queues is svc._QUEUES`
  — the original registry, not a replica. Restart behavior simulated
  without a process restart: unregister + live-store clear returns
  the session to idle/None (covered by clear/unregister tests).
  Discrimination: cancel-guard-disabled mutation fails the cancel
  suppression + end-to-end tests; restored byte-identical.
- Gates (same env/invocation/scope: `.venv`, `pytest -q -p no:warnings
  src/tests/ --ignore=src/tests/unit`, unit exclusion still the proven
  `test_security.py` collection error): focused + neighbors
  (live-status, launch-gate, steer/stop/followup/restart/supervised)
  green; broad **28 failed / 1910 passed / 79 skipped** with sorted
  FAILED identities `diff`-clean against the P4-1 baseline
  (28/1893/79; +17 = new tests, −0/+0 failures).
- Spend audit: `CUTTLE_AGENT_SMOKE`/`CUTTLE_ALLOW_SPEND` unset (verified
  zero in this shell); no paid/token prompt tests run (no executor
  paths touched).
- Manual validation N/A (backend service; no UI change). No Flask
  restart/kill performed.
- Files: `src/api/chat_status.py` (new),
  `src/api/chat_status_phases.py` (injected `emit_fn`),
  `src/api/web_chat_api.py` (import + alias + wrapper + register/
  unregister/call-site delegation),
  `src/tests/test_chat_status_service.py` (new), this section +
  `docs/architecture/repository-map.md` (service row).
- Remaining P4-3/closure: small pure helpers (`_default_chat_cwd`,
  badge/metadata fns — verify no hidden state first). Runner fns,
  coordinator, and router brains stay for Phase 5/6. Completion per
  P4-1 criteria: production reverse imports only the Phase-5 runner
  calls + `internal_http` transport (same grep method).
- Commit independently on main. No push, no restart.
  **STOP for Codex review before P4-3 or Phase 5.**

## P4-2 approval (Codex) + Phase 4 P4-3 helper boundaries and Phase 4 closure

- P4-2 `3b973f7e` APPROVED. Reviewer last read line 5233; this
  section appended only below that boundary; prior sections preserved.
- P4-3 scope (no coordinator/router redesign): the remaining
  production reverse imports into `web_chat_api.py` were helper-level
  only. Before/after census, same grep method
  (`grep -rn web_chat_api src/ --include=*.py`, non-test, plus
  `importlib`/`__import__`/dynamic-`getattr` pattern sweep which found
  zero dynamic imports):
  - BEFORE (8): `chat_delivery`, `auth_api`, `supervised/orchestrator`
    (removed P4-1/P4-2), `agent_harness/kernel._fallback_chat_cwd`,
    `subagents/turns`, `subagents/identity`,
    `internal_http._internal_app_post`, `agent_router/dispatch`,
    `supervised/adapters` x2, `doctor` probe.
  - AFTER (4, all Phase-5-or-justified): `agent_router/dispatch:48`
    + `supervised/adapters:103,123` (runner fns reserved for Phase 5),
    `doctor:83` (lazy smoke probe, justified remainder). Zero helper
    reverse imports; zero dynamic-pattern imports.
- Moves (verbatim behavior, thin `wca` aliases preserve callers):
  - NEW `src/api/chat_metadata.py`: `muse_model_label`,
    `usage_meta_from_assistant_result`, `user_badge_metadata`. Pure
    functions; module imports `typing` only; zero module-level mutable
    state (verified by grep — no hidden state). `subagents/turns.py`
    and `subagents/identity.py` import the owner directly
    (`cursor_run` passthrough kept inline in `turns`, same shape).
  - `managers/project_manager.py` owns `default_chat_cwd(pm, root)`
    + `REPO_ROOT`; `pm` is a required explicit arg (a `None` fallback
    to the singleton was rejected by test pin
    `test_default_cwd_owner_explicit_inputs`, now explicit). Kernel
    `_fallback_chat_cwd` passes `(_pm, REPO_ROOT)` explicitly.
  - `internal_http.py`: dead in-process `test_client().post` branch
    deleted (zero callers; `_internal_use_http_dispatch` +
    `_internal_app_post_lock` removed with it); module is now
    loopback-HTTP-only, transport seam explicit. `wca` import in that
    module gone.
- Lifetimes: `chat_metadata` is stateless (safe anywhere);
  `default_chat_cwd` reads the process `project_manager` singleton
  passed explicitly — no per-request caching, no restart-sensitive
  state moved. Phase-4 lifetime rule holds (process state never into
  per-request objects; restart behavior unchanged — singleton
  re-reads `projects.db` on next process start as before).
- Coverage (committed `src/tests/test_chat_metadata_service.py`,
  8 tests, all green): 5 behavior pins written/run BEFORE the move
  (badge slash chips, usage extraction incl. empty/malformed,
  model-label mapping, cwd preference order, alias delegation) + 3
  post-move (owner import guard, explicit-input signature, alias
  identity both directions). Discrimination: cache-merge-swap
  mutation failed the usage tests as required, restored
  byte-identical (`cmp` clean).
- Gates (same env/invocation/scope: `.venv`,
  `pytest -q -p no:warnings src/tests/ --ignore=src/tests/unit`):
  all three Phase-4 service suites green together (38 passed);
  focused + neighbors (steer/stop/followup/restart/supervised,
  subagents, harness) green; broad post-P4-3 **28 failed /
  1918 passed / 79 skipped** with sorted FAILED identities
  `diff`-clean against the same-env stashed P4-2 baseline (28/28,
  byte-identical list — pre-existing failures only, listed in gate
  artifacts `/tmp/p42base_failed.txt` vs `/tmp/p43_failed.txt`).
- Spend audit: `CUTTLE_AGENT_SMOKE`/`CUTTLE_ALLOW_SPEND` unset; paid
  smoke tests appear in the gate only as SKIPPED; no paid/token
  prompt tests run.
- Manual validation N/A (backend-only moves; no UI change). No Flask
  restart/kill performed. No push.
- Files: `src/api/chat_metadata.py` (new),
  `src/tests/test_chat_metadata_service.py` (new),
  `src/api/web_chat_api.py` (aliases + delegation, net −~270 lines),
  `src/api/subagents/turns.py`, `src/api/subagents/identity.py`,
  `src/api/agent_harness/kernel.py`, `src/api/internal_http.py`,
  `src/managers/project_manager.py`, this section +
  `docs/architecture/repository-map.md` (P4-3 ownership row).
- Limits/risks: metadata shapers have many lazy leaf callers —
  checked for cycles (owner imports `typing` only; none possible).
  `turns.py` keeps a small inline `cursor_run` passthrough rather
  than forcing it into the owner — deliberate, documented above.
  Deferred dev dependency/preflight tooling still deferred; stale
  baseline pins untouched.
- Phase 4 CLOSURE: all Phase-4 service interfaces own explicit
  inputs/lifetimes (live-status store, status-queue registry + emit,
  metadata shapers, chat cwd, loopback transport seam); production
  reverse imports into the entry module are only the Phase-5 runner
  calls + transport + one justified probe. Coordinator
  (`process_message_with_bot`) and router/harness redesign reserved
  for Phase 5/6 — untouched.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before Phase 5.**

## P4-3 approval + census reconciliation + Phase 4 readiness/closure + P5-A

- P4-3 `c0ad8789` APPROVED (pending this factual clarification).
  Reviewer last read line 5320; this section appended only below that
  boundary; prior sections preserved.
- Census reconciliation (exact, same method: static
  `from api import web_chat_api` / `from api.web_chat_api import` /
  `import api.web_chat_api` over `src/`, non-test, plus an
  `importlib`/`__import__`/`import_module(api)` sweep — zero dynamic
  hits both revisions):
  - BEFORE is `3b973f7e` (P4-2 HEAD), not pre-Phase-4: 8 import
    **sites** in 7 files — `agent_harness/kernel.py:71`
    (`_default_chat_cwd`), `internal_http.py:61` (`as wca`,
    in-process POST), `subagents/identity.py:106`
    (`_user_badge_metadata`), `subagents/turns.py:92`
    (`_assistant_message_metadata`), `agent_router/dispatch.py:48`
    (`w._run_harness_web_command`), `supervised/adapters.py:103`
    (`w._run_codex_web_command`), `supervised/adapters.py:123`
    (`w._run_cursor_web_command`), `doctor.py:83` (import-only
    smoke probe). Correction: the P4-3 report prose wrongly listed
    `chat_delivery`/`auth_api`/`supervised/orchestrator` under
    BEFORE — those were removed by P4-1/P4-2 and are absent at
    `3b973f7e`. The "8" count itself was correct as a site count.
  - AFTER is `c0ad8789`: 4 sites in 3 files — `dispatch.py:48`,
    `adapters.py:103`, `adapters.py:123` (all Phase-5 runner fns),
    `doctor.py:83` (probe). P4-3 removed exactly the 4 helper sites.
  - "Transport remains" clarification: `internal_http` no longer
    imports the entry module at all — what remains is the
    loopback-HTTP transport *capability* as the explicit seam (dead
    `test_client` branch deleted), not a reverse import.
- Phase 4 readiness map (all required turn-lifecycle interfaces have
  owned, tested services — no essential gap, so no P4 follow-up;
  coordinator extraction may proceed):
  - Session access: `AuthDatabase` (`api.auth_db`), process singleton
    via `get_auth_db()` over `DB_PATH`; chat sessions + messages +
    followup queue are SQLite rows (survive restart).
  - Live status: `api.chat_live_status` (process dict + lock, 45-min
    TTL); entry keeps thin wrappers injecting the delivery cancel
    predicate.
  - Delivery: `api.chat_delivery` — turn tokens
    (`try_begin`/`current_turn`/`is_stale_turn`), busy lock
    (`is_busy`/`reconcile_zombie_busy`), result store
    (`store_result`/`take_result`); process state, never per-request.
  - Cancel: `FailureKind.CANCELLED` (`agent_router/types` + `policy`
    + `dispatch` — terminal, never escalates) + delivery turn guard
    + `chat_run_registry.cancel_session_runs`.
  - Steer: `api/agent_harness/steer.py` registry
    (`register`/`unregister`/`steer`, per-session token).
  - Followups: `auth_db` followup queue (`get`/`take_followup_queue`,
    SQLite-persisted) + supervised `user_followup` control lane.
  - Assistant persistence: `auth_db.add_message` /
    `update_message_content` / `merge_message_metadata_by_query`.
  - Execution status: `api.active_executions` (process dict,
    stale TTL) + `api.chat_run_registry` (procs/pids/kill-tree).
  - Restart/recovery: `api.flask_restart` (file-protocol state
    machine: ack + `restart_id` → daemon request → status file +
    chat completion) + `restart_safety_policy`.
  - Remaining coupling (not gaps): delivery + run-registry dual-key
    busy tracking by session; runner fns (`_run_*_web_command`)
    still live in the entry module — P5-B target.
- Factual Phase 4 CLOSURE: helper reverse imports eliminated (4
  helper sites → 0); stateless/pure shapers, explicit-input cwd,
  loopback-only transport, and all nine lifecycle interfaces above
  are owned with explicit contracts and process/SQLite lifetimes.
- P5-A (this checkpoint): transport-neutral turn seam, no workflow
  move, no router redesign, no behavior change.
  - Inventory: `/api/chat` route (~1100 lines: auth → /restart →
    supervised lane → session resolve/pins → sticky/star → prepass →
    identity → launch gate → … → `process_message_with_bot` at two
    call sites + `_generate_chat_stream`); `process_message_with_bot`
    selection head (launch gate → native /restart → harness slash +
    mode block → router → `_no_pipeline_chat_result` fallback);
    `_execute_remote_agent_tool`/runner fns stay for P5-B.
  - NEW `src/api/chat_turn.py`: `strip_invisible_leading`,
    `parse_stream_flag`, `TurnRequest`/`normalize_chat_post` (400
    strings preserved as data), `TurnSelection`/`classify_selection`
    (restart → harness/mode-blocked/empty-prompt → router; narrow
    injected `match_harness`/`is_restart`/`cloud_blocked`),
    `split_db_session_id`, `build_turn_context`. Imports `api`
    leaf (`inference_mode`) only — no entry-module import.
  - Delegation: route head → `normalize_chat_post` (400s
    byte-identical); coordinator entry-strip, harness/mode/empty
    arm, `db_session_<int>` parse, and context dicts → seam.
    One documented precedence note: a hypothetical future harness
    manifest named `/restart` would now lose to the native arm at
    classify time (pre-seam it ran only when the native handler
    threw); no such manifest exists in-repo (verified — zero
    `restart` in catalog/agents), so in-repo behavior is identical.
  - Coverage (`src/tests/test_chat_turn_seam.py`, 24 tests green):
    16 seam pins (normalize edges incl. all stream forms,
    invisible chars, both 400s, attachment-only; all classify arms
    with fake matchers; db-sid parse; context shape) written BEFORE
    the move against documented behavior, failing at collection
    pre-implementation; 6 coordinator integration tests with the
    REAL `process_message_with_bot` + fake executor/router (harness
    arm via real catalog matcher, mode-blocked, empty-prompt,
    router db-sid, fallback, restart-handler-failure fallthrough);
    2 route tests through the real Flask test client (both 400
    contracts). Strip parity proven differentially
    (`STRIP_IDENTICAL` over BOM/ZW cases).
  - Gates (same command/env/scope/revision `c0ad8789`+work):
    seam 24/24; neighbors (metadata/live/status/steer/
    supervised-coordinator) green from repo-root cwd — note: two
    supervised JS-pin tests fail only when pytest runs from `src/`
    (cwd artifact, pre-existing module `chdir` behavior, also true
    on baseline; canonical root-cwd run is clean). Broad **28
    failed / 1942 passed / 79 skipped** with sorted FAILED
    identities `diff`-clean vs the P4-3 baseline (`/tmp/
    p43_failed.txt` vs `/tmp/p5a_failed.txt`); +24 = new seam
    tests, −0/+0 failures.
  - Spend audit: `CUTTLE_AGENT_SMOKE`/`CUTTLE_ALLOW_SPEND` unset;
    no paid/token prompt tests run (fakes only). No process
    restart/kill. Manual validation N/A (backend-only; no UI).
- Files: `src/api/chat_turn.py` (new),
  `src/tests/test_chat_turn_seam.py` (new),
  `src/api/web_chat_api.py` (route head + coordinator head
  delegate; execution/persistence/delivery untouched), this section
  + `docs/architecture/repository-map.md` (P5-A seam row).
- Remaining P5: P5-B core workflow ownership (execution/resume/
  progress/cancel/persistence/delivery/post-turn extraction with the
  runner reverse imports); router/harness redesign stays Phase 6.
  Deferred dependency/startup tooling unchanged.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before P5-B/Phase 6.**

## P5-A approval + P5-B core turn-workflow ownership

- P5-A `3ff1f8bf` and the factual Phase-4 readiness/closure
  APPROVED. Reviewer last read line 5445; this section appended only
  below that boundary; prior sections preserved.
- P5-B inventory (actual analysis, corrected mid-work): the ROUTE is
  the real coordinator for authed web turns — `process_message_with_bot`
  serves only local-mode/Discord/`sessions_send`/leftover paths. Route
  lanes found: `/restart` native, supervised control lane,
  router-family lane (`/route|/retry|/router|/coordinate`, sync +
  stream via `_make_auth_assistant_saver`), cloud-mode block, harness
  lane (`_match_harness_slash` → empty-prompt / busy-reject / sync /
  stream), `/pipelines` notice, project-action/action-form no-LLM
  lanes, launch gate, leftover pipeline lane
  (`process_message_with_bot` sync + stream). `_execute_remote_agent_tool`
  no longer exists (AGENTS.md stale on that point); execution is
  `_run_pinned_harness_turn` → `_run_harness_web_command` →
  `kernel.run_agent_web_command` plus `maybe_route_plain_message`.
  Each sync lane repeats busy → persist-user → run → post-turn → release.
- New `src/api/chat_turn_workflow.py` (no Flask, no DB, no vendor
  code; deps = narrow callables + `chat_delivery` service object):
  - `run_agent_sync_turn` — harness + router-family sync order
    (persist → run → after_run → 200; busy → 409; run() errors
    propagate after release — matches both lanes' 500 behavior).
  - `run_pipeline_sync_turn` — leftover order with the
    success + stale/cancel no-persist gate and rewrite-aware
    `save_assistant` replacement.
  - `finalize_stream_result` — stream completion tail (save →
    notify → park → release-by-token; superseded saves/parks
    nothing but still releases; save errors contained).
  - `build_pipeline_body` (verbatim sync body incl
    query/report/run/usage), `busy_response_body`,
    `is_turn_superseded`, `begin/release_sync_turn`.
  - State diagram: route resolves + serializes; coordinator calls
    `delivery.try_begin → current_turn → (persist → run →
    save/notify/park) → end(turn=token)`; SQLite rows via injected
    callables; parked results via delivery. One mutable-state source
    (`chat_delivery` + SQLite); no per-request caches added.
- New `src/api/agent_harness/runners.py`: all seven runner entries
  (`run_harness_web_command`, `run_pinned_harness_turn` with
  pinned-outcome recording, cursor/codex/muse/claude/hermes shims)
  moved verbatim. `dispatch.py:48` + `supervised/adapters.py:103,123`
  now import the owner — production reverse imports into the entry
  module are down to the single justified `doctor.py:83` smoke probe
  (verified by grep; dynamic-pattern sweep still zero). Entry keeps
  same-name aliases, so existing callers/tests patching
  `wca._run_*` keep working (no test needed retargeting for that).
- Delegation: router sync lane, harness sync lane, leftover pipeline
  sync lane, and the `_generate_chat_stream` completion tail call the
  owner; route keeps auth/busy-reject/SSE framing/`jsonify`,
  `is_busy` zombie-heal pre-guard, stream thread + pump, saver
  construction (`_make_auth_assistant_saver` stays — it closes over
  the Flask request via `_current_request_data`; P5-C candidate).
  Precedence/semantics untouched; executor errors still 500 on the
  route lanes and contained only inside `process_message_with_bot`.
- Coverage (`src/tests/test_chat_turn_workflow.py`, 15 tests green):
  6 pre-move route/stream integration pins written against current
  code (real `/api/chat` sync: order + badge meta + release; 500 +
  user-only + release on executor error; 409 busy with zero rows;
  cancel-mid-run → 200 + user-only via saver guard; real
  `_generate_chat_stream`: claim→run→save order + park + release;
  cancelled stream: SSE still delivers reply, saves/parks nothing).
  Two pre-move assertions were corrected to real semantics found
  during pinning (assistant badge needs `cursor_run`; harness lane
  has no superseded gate — the saver cancel-guard does that work;
  executor errors 500 rather than 200). 8 owner unit tests (order,
  409 shape, exception-release, failure/supersede gates, body
  shape, finalize order/skip/contain) + 1 architectural pin (12
  owner/router files contain no `web_chat_api` string).
  Discrimination: persist-after-run mutation failed 3 tests
  (order unit + 2 route integrations); restored byte-identical.
- Gates (same command/env/scope; baseline `3ff1f8bf`): focused
  workflow+seam 38/38; neighbors 208 passed with 3 failures all
  proven pre-existing (palette pin in the 28; hardening idempotent
  fails identically on stash; node helper passes with the nodeshim
  PATH the gate uses — my neighbor invocation lacked it). Broad
  **28 failed / 1957 passed / 79 skipped**, sorted FAILED
  `diff`-clean vs P5-A baseline (`/tmp/p5a_failed.txt` vs
  `/tmp/p5b_failed.txt`); +15 = new workflow tests, −0/+0 failures.
- Spend audit: flags unset; fakes only; no paid prompts, no process
  restart/kill. Manual validation N/A (backend-only).
- Files: `src/api/chat_turn_workflow.py` + `src/tests/
  test_chat_turn_workflow.py` + `src/api/agent_harness/runners.py`
  (new); `src/api/web_chat_api.py` (3 lanes + stream tail delegate;
  runners are aliases); `src/api/agent_router/dispatch.py`,
  `src/api/agent_router/supervised/adapters.py` (owner imports);
  this section + repository-map (P5-B row).
- Limits/risks: `_generate_chat_stream` thread + SSE pump stay in
  the entry (transport); saver construction still request-bound;
  `process_message_with_bot` keeps its own selection+fallback head
  (Discord/local callers). Remaining P5: P5-C (saver/persist-user
  ownership with explicit request-data + stream-lane closures,
  `process_message_with_bot` retirement decision); router/harness
  redesign stays Phase 6. Deferred tooling unchanged.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before the next checkpoint/Phase 6.**

## P5-B approval + P5-C persistence boundary and Phase 5 closure

- P5-B `973bd17e` APPROVED. Reviewer last read line 5542; this
  section appended only below that boundary; prior sections preserved.
- P5-C inventory: `_make_auth_assistant_saver` (130-line closure over
  Flask `request` via `_current_request_data` + `get_auth_db`
  singleton), `_persist_user_turn` closure (badge/history/project via
  `_persist_auth_user_message`, itself request-bound),
  `_current_request_data` (thread-local re-read at save time),
  stream lane triples (router/harness/pipeline `_run` + saver +
  `_claimed` — 3-line wirings once saver/persist are owned),
  `process_message_with_bot` callers (route leftover sync, leftover
  stream, `/api/sessions/send` — verified zero Discord callers;
  Discord is optional agent-ops only, nothing revived).
  `_resolve_request_project_path(data)` is pure-given-data (no Flask
  reads) — safe to inject as `resolve_project`.
- New `src/api/chat_turn_persist.py` (no Flask/db-singleton/request
  reads — verified by scan; leaf imports only): `make_assistant_saver`
  (skip guards verbatim), `persist_user_turn` (badge/history/project),
  `persist_auth_user_message` — all with explicit `db` +
  `request_data` (+ `resolve_project`/`assistant_meta_fn`/
  `project_merge_fn`/`schedule_autoname`/`badge_fn`/`project_root`).
  Entry wrappers (`_make_auth_assistant_saver`,
  `_persist_auth_user_message`, `_persist_user_turn`) only inject
  shapers and capture the already-parsed body once in the request
  thread. `process_message_with_bot` kept as a documented
  compatibility entry (no new surfaces; selection/execution already
  owned) — not retired, not duplicated.
- Delegation: saver construction + user persist call the owner in all
  lanes; stream triples compose owner-built pieces; SSE pump/thread/
  framing stay bounded transport. Exactness fixes found during the
  move: owner keeps the empty/no-session no-op guard and the
  merge-inside-try containment of the original.
- ONE reported boundary delta (not silent): saver/request capture
  happens once at build (request thread) instead of re-read at save.
  Sync saves are identical (same thread, same body). Late
  stream-thread saves previously re-read `{}` (no request context)
  and now merge the captured body — streamed assistant rows gain the
  project stamps the sync path always had. No test depended on the
  gap (broad gate clean); the old re-read looked accidental, but it
  is recorded here rather than claimed as zero-drift.
- Known pre-existing lane divergence (deferred, unchanged): the
  pipeline stream `on_save` persists `[CANCELLED]`/`system` rows
  while the saver-built lanes skip them. Out of P5-C scope; flagged
  for a separately scoped defect pass, not fixed silently here.
- Stale-doc corrections: `_execute_remote_agent_tool` and
  `POST /api/execute-tool` no longer exist anywhere in `src/`
  (verified zero hits) — AGENTS.md dispatch bullet and the
  repository-map graph-era paragraph now point at
  `runners`/`chat_turn*` owners.
- Coverage (`src/tests/test_chat_turn_persist.py`, 15 green): 12
  owner pins (badge/project/history/empty/merge-error guards; all 5
  saver skips; cancel-guard; auth-message merge/no-op) written
  BEFORE the owner existed (collection-error pre-failure) + 1 real
  route SSE integration (stream 200, session event + response + done
  chunk, both rows, release) + 2 guard tests added with the
  exactness fixes. Discrimination: `[CANCELLED]`-guard removal fails
  the parametrized skip test; restored byte-identical. One
  pre-existing structural pin (`test_first_turn_agent_pins` badge
  substring) was retargeted to the new shape — route order +
  delegation contract + owner call-site assertion, with chip landing
  still proven behaviorally by the owner test — not weakened.
- Gates (same command/env/scope; baseline `973bd17e`): focused
  persist+workflow+seam 54/54; pins 14/14; broad **28 failed / 1972
  passed / 79 skipped**, sorted FAILED `diff`-clean vs P5-B
  (`/tmp/p5b_failed.txt` vs `/tmp/p5c_failed2.txt`); +15 = new
  persist tests, −0/+0 failures. The retargeted pins test passes in
  both runs.
- Spend audit: flags unset; fakes only; no paid prompts, no process
  restart/kill. Manual validation N/A (backend-only).
- Files: `src/api/chat_turn_persist.py` + `src/tests/
  test_chat_turn_persist.py` (new); `src/api/web_chat_api.py`
  (saver/persist wrappers delegate, dead `on_save` body excised
  net −~130, compat docstring); `src/tests/
  test_first_turn_agent_pins.py` (retargeted pin); AGENTS.md +
  repository-map (stale executor pointers corrected, P5-C row).
- Phase 5 CLOSURE matrix: P5-A envelope/selection ✓, P5-B lane
  orchestration + runner relocation ✓, P5-C persistence boundary +
  stream composition + compat decision + stale-doc cleanup ✓.
  Entry-module production imports: only the `doctor` probe. All
  turn paths (sync/SSE × harness/router/pipeline/control/no-LLM,
  cancel/stale/409/500) owned or explicitly compat-wrapped.
  Remaining non-gaps for Phase 6+: SSE pump/thread extraction
  (transport), `_generate_chat_stream` caller triples, saver
  per-lane specialization review (incl. the deferred
  `[CANCELLED]` divergence), `sessions_send` contract preservation.
  Deferred dependency/startup tooling unchanged.
- Commit independently on main. No push, no restart.
  **STOP for Codex review before Phase 6.**
