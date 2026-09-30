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
