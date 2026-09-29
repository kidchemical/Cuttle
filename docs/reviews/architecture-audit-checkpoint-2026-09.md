# Architecture audit checkpoint — 2026-09-28 (CH-000761 → CH-000764)

Investigation pass only. Settled prior work is not reopened: HTTP read/write
auth split + installer hardening (sha pins, size cap, npm spec validation),
each with regression tests. Source: four parallel read-only research sweeps
plus direct inspector greps; see "Inspected vs inferred" below.

## CH-000764-6 status

Finished. `/project` + Pending Changes regression fixed (16 decorator swaps:
GET reads → `authenticated_required`, mutations stay `owner_required`) with 4
regression tests in `src/tests/test_http_authz.py`. 77 focused tests green.
Live Flask restart still required to pick the fix up; operator identity aligned
(`OWNER_USER_EMAIL=ian`). Incidental fix in the same session: `.env.example`
reordered by significance, dead keys removed, mesh keys documented.

## P1 — Retired pipeline dependency closure (inspected)

OBSOLETE (remove, in dependency order):
- ~20× 410 tombstones via `_graph_pipelines_gone_response`
  (`web_chat_api.py:392`): trigger-schedule, telegram, slack,
  settings/pipeline-routing, pipeline-limits, save/list/load/delete,
  execution-start/finish, check-running, run-now, schedule-toggle, stop,
  register-running, job-status, start, record-node, pipeline-settings
  default/reset/auto-start/delete, execute-tool/output.
- Daemon dead loop: `reload_pipelines` (920), `watch_pipelines` (1154),
  `_auto_start` (1313), watcher thread (1481), scheduler (1485),
  `run_schedule_loop` (1056) — polls 410/empty endpoints post-cleanup.
- `settings_manager` keys: telegram/slack channels, `pipeline_routing`,
  `telegram_user`/`slack_user`, `allowed_pipelines*`, `pipeline_limits`,
  default/factory/last_opened/recent pipeline keys.
- Frontend dead callers: `startJob/stopJob/runNow/toggleSchedule`,
  `loadPipelines` stub, 4 dead 410 callers in `job_insight`,
  chat palette `pipelines=[]`, pipeline-only copy/aria labels.
- No `_handle_external_trigger` remains in tree (telegram/slack are stubs).

REQUIRED (keep): `cuttle_jobs` + `device_workers` daemon loops, live
`/api/cuttle-jobs`, `jobs_page` live fetch, `job_insight` live
`/api/job-insight` logic (reads pipeline JSON + query logs).

DECISION-ONLY (needs a human keep-or-remove call, no test pins today):
200 shims (`list-pipelines []`, `schedule-triggers []`, `/api/jobs` empty,
`pipeline-reload reloaded[]`, `running-pipelines` live-empty),
`pipeline-chats` fs endpoints (real fs, no writers), sandbox
`allowed_pipelines`/`restrict_for_session_kinds` inert fields.
Only 410 pins exist in tests (`test_http_authz:757`,
`unit/test_remote_agent:43`, `discord_gateway_retired:22`).

## P2 — web_chat_api.py dependency map (inspected core, plan below)

- 229 `@app.route` in `web_chat_api.py`; 3 blueprints registered
  (`auth_bp:297`, `workers_bp:302`, `dashboards_bp:307-308`).
- 10 non-test modules import from it (`agent_harness/kernel`,
  `internal_http`, `chat_delivery`, `auth_api`, `subagents/turns`,
  `subagents/identity`, `agent_router` supervised orchestrator/adapters/
  dispatch, `chat_status_phases`) — extraction must preserve these surfaces.
- Shared globals: `chat_sessions {}`, `session_counter`, live-status TTL maps,
  `_OWNER_EMAIL` import-time snapshot (restart-sensitive), cert paths.
- Restart-sensitive: in-memory sessions/live-status/pending action-forms do not
  survive Flask restart (recovery via HMAC spec tested); generation files
  coordinate daemon restarts.

## P3 — Adapter methodology (bounded; gap remains)

Established (prior sessions, manifests + kernel inspected): manifest +
adapter + shared kernel holds; bundled-first discovery; project adapters gated;
`sys.path.append` never `insert(0)`; 8 adapters implement the full Protocol.
Missing this pass: per-adapter `execute()` argv/quoting/lifecycle inspection
(P3 child returned no complete evidence). No change recommended without that
evidence. See workstream 5.

## P4 — Coverage reconciliation (inspected)

Dashboards blueprint fully inventoried: `GET /api/dashboards` (`''`/`'/'` →
hub), `/<dash_id>` (404 unknown), `model-benchmarks`, `cuttle-performance`,
`cuttle-usage`, all `@owner_required`; `catalog.py` 3 live ids + helpers;
`service.py` hub/benchmarks (deepswe/swebench/aider/aggregate)/performance +
stub; `usage.py` `USAGE_ID=cuttle-usage`. Tree: 916 tracked files, clean
status at sweep time, `dashboards/*` 11 tracked `.py`, `.gitignore` reviewed.
No conflict with prior inventory. Note: `test_jev.py::test_flask_performance_route`
fails against the owner-gated dashboards blueprint — pre-existing, same
over-gating pattern, deliberately left for its own workstream.

## Inspected vs inferred

Inspected this pass: route/decorator maps (HEAD-vs-tree diff + live grep),
daemon thread sites, settings keys, frontend dead callers, dashboards
blueprint + service + catalog bodies, blueprint registrations, reverse-import
list, module globals, users/projects table schemas, `.env` loading paths.
Inferred (not body-verified): exact per-subsystem route counts, per-adapter
argv behavior, 200-shim consumer absence (grep-negative only).

## Workstream 1 record (executed 2026-09-28, CH-000764)

Baseline: HEAD `01efd3e`, tree clean except hub-rule sync. No prior-work
conflation: prior auth/installer fixes were already committed (`edc528d`).

Regression checkpoint first: live Flask (restarted, PID 307796) verified —
guest `GET /api/projects` → 200, guest pending-changes → 200 (was 403).
Dashboard failure recorded separately: `test_jev.py::test_flask_performance_route`
`KeyError: 'id'` — pre-existing (owner-gated dashboards blueprint vs
unauthenticated test), untouched.

200-shim / pipeline-chats decision (evidence, not vibes):
- Removed: `list-pipelines`, `pipeline-schedule-triggers`, `pipeline-reload`,
  `running-pipelines`, `/api/jobs`, `pipeline-chats` ×2. Zero frontend
  callers, zero tests, `output/pipeline_chats/` absent on this install.
  On-disk chat files (if any elsewhere) are untouched — endpoints removed,
  data not deleted. `running_pipelines = {}` dict KEPT (live readers:
  `/api/status`, `/api/sessions/list`); renamed to
  `retired_pipeline_registry` 2026-09-28 with shape preserved
  (`running_pipeline_count: 0`).
- Kept deliberately: `/api/sessions/send` (live session-to-session; only the
  `target_pipeline` branch removed), pipeline palette builder/resolver
  (renders historical `/pipeline` messages), `node_editor.html` redirect.

Removed (per subsystem, closure-audited):
- Flask: 24× 410 tombstone routes + `_graph_pipelines_gone_response` (229→198
  routes). `_legacy_process_control_gone_response` KEPT (test-pinned 410).
- Daemon: `_auto_start_default_pipeline`, `reload_pipelines`,
  `watch_pipelines_and_reload`, `run_schedule_loop`,
  `_get/_fire_schedule_trigger`, cron helpers, 3 thread starts. Preserved:
  notify/health/restart/home-automation/cuttle-jobs/device-workers loops.
- Settings: telegram/slack channel defaults, `pipeline_routing` key + methods,
  `last_opened/recent_pipelines`, `auto_start_default`, sandbox
  telegram/slack kinds. KEPT: `get_pipeline_limits` (health),
  `get_default_pipeline`/`factory` (doctor/wizard), `allowed_pipelines` param.
- Frontend: jobs Pipelines tab + 9 dead functions + dead listeners;
  job_insight actions → read-only note. KEPT: insight display, countdowns.
- Tests: 2 pins updated 410→404 (intentional removal documented in-test).
- Docs: `WEB_CHAT_API.md` pipeline-settings cluster entry.

Verification: `test_http_authz` + `test_remote_agent` + `test_daemon_startup_ui`
→ 57 passed; neighbors (jev/meta/pending/palette) → 62 passed + 1 known
pre-existing dashboard failure; py_compile on all touched Python; `node
--check` on all 3 edited inline page scripts; zero dangling references
(`grep` for every removed symbol). No unexecuted workflow claimed passing.

## Settings extraction record (executed 2026-09-28, CH-000764)

New module `src/api/settings_routes.py` (`settings_bp`, prefix `/api`)
owns validation, persistence, defaults and authorization for 9 families
(15 routes): bot, lan-access, channels, sandbox, starred-slash,
starred-project, ui-layout, video-background, app-settings. Family table
`SETTING_FAMILIES` names backend + validator + auth per group; writes are
uniformly owner-gated by decorator (redundant inline `require_owner()`
checks in sandbox/lan-access POSTs removed — decorator is now the single
enforcement point). Monolith drops 182→166 routes; blueprint registered
beside auth/workers/dashboards. To add a setting: validator + handler here,
persist via `settings_manager` (app) or `core.config` (bot) — no monolith
edits.
Equivalence proof: all 16 moved handler bodies diffed against HEAD —
identical modulo the two hoists (channel/lan/ui-layout validators, same
conditions/messages) and the auth-dedup. Contracts frozen: paths, methods,
status codes, payloads, gates (including open `GET /app-settings`).
Preserved live: health `pipeline_limits`, doctor/wizard default-pipeline
methods (manager untouched).
Tests: new `test_settings_routes.py` (5 tests: path presence, read/write
auth matrices incl. non-owner 403, owner round-trip + legacy-key drop +
frozen 400s, validator/registry ownership). Suites: settings+auth+jev →
75 passed. Pre-existing, untouched, recorded: `test_ui_layout_apps`
2 failures (tests share live `settings.json`; user's real `rail_hidden`
breaks pristine-state assumptions), `test_agent_defaults…capability`
401-vs-400 (auth-before-validation precedence on an untouched route).
Self-inflicted note: the first round-trip test version wrote
`ui_layout.rail_items=["chat","jobs"]` to live `src/settings.json`
(original unknown); test now uses an isolated tmp settings file and the
matrix test performs no owner writes. Rail heals on next UI layout change;
flagging so the owner can re-pin if order looks off.

## Workstreams (small, independently verifiable)

1. Dead daemon threads removal (P1 loop sites) — verify daemon starts with
   only cuttle_jobs/device_workers loops.
2. ~20× 410-route removal + gone-response family — update 410-pin tests.
3. Obsolete settings keys + dead frontend callers — verify jobs live paths.
4. 200-shim / pipeline-chats keep-or-remove decision + one pinned test each.
5. Per-adapter `execute()` argv/lifecycle inspection (P3 gap) — no code change.
6. Dashboards 410/owner-gate test repair (pre-existing failure).
7. P2 extraction slices, in plan order below.
