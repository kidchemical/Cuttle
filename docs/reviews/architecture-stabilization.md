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
