# `web_chat_api.py` dependency map

**Status:** living architecture reference. **Do not treat this file as a license to extract code.**
**File:** `src/api/web_chat_api.py` (~9,856 lines; 139 `@app.route`
decorators over 132 unique paths, counted from the AST at 2026-10-01,
HEAD `4845233c`).
**Audience:** agents and humans about to change Flask/chat/settings/workers.

Cuttle’s Flask **app object** lives here. Many product surfaces already have their own modules; this file is still the **composition root** (blueprints, HTML, chat-turn HTTP, process-control 410s).

When you add a backend feature:

1. Read this map and prefer an existing package (`api.agent_harness`, `api.device_workers`, `api.auth_api`, `api.dashboards`, …).
2. Do **not** add a new import from `web_chat_api` into a leaf module if a helper can live next to its owner.
3. Do **not** start a structural extraction unless product work is blocked (regressions, duplicated routes, or the file is slowing the change). Extraction is a separate initiative, not a prerequisite for building Workers/GUI/dashboards. Completed extractions (settings, projects, Git, action-forms, live-status/status-queues, harness runners) are done — do not re-extract them.

Related: [`MODULARITY.md`](MODULARITY.md), [`docs/ROADMAP.md`](../ROADMAP.md), and [extension boundaries](../architecture/extension-boundaries.md).

Tracked (historical references, not verified current status — do not
implement in the same breath as product features; #3 predates the
BYO-CLI installer retirement):

| Issue | Topic |
|---|---|
| [#1](https://github.com/kidchemical/Cuttle/issues/1) | Worker identity / LAN enroll / loopback runtime auth |
| [#2](https://github.com/kidchemical/Cuttle/issues/2) | Electron TLS pin / fingerprint |
| [#3](https://github.com/kidchemical/Cuttle/issues/3) | Adapter capabilities + installer checksums |
| [#4](https://github.com/kidchemical/Cuttle/issues/4) | Unauthenticated settings GET |
| [#5](https://github.com/kidchemical/Cuttle/issues/5) | HMAC replay policy (per action class) |
| [#6](https://github.com/kidchemical/Cuttle/issues/6) | Flask modularization (this map is the starting point) |

---

## What this file is

| Role | Notes |
|---|---|
| Flask `app` factory-in-place | TLS cert helpers, CORS-ish headers, rate limiter, static/HTML routes |
| Chat-turn HTTP ingress | `POST /api/chat`, `process_message_with_bot` (compat), SSE stream, steer/cancel; decisions in `chat_turn` / `chat_coordinator` / `chat_turn_workflow` |
| Process / LAN / restart HTTP | `/api/flask/restart*`, `/api/status`, LAN settings, process-control 410 stubs |
| Grab bag of product HTTP | router/agent palettes, per-harness pins, shell panes, sessions, TTS/widget/terminal/mobile registers, usage-live |

**Daemon contract:** `cuttle_daemon` spawns this module as the Flask child on port **8080**. Coordinated restart is `flask_restart` / `/restart` — never `taskkill` the API from an agent it hosts.

---

## Already extracted (mounted from here)

Registered near the top of `web_chat_api.py`. `auth_bp` and `usage_live_bp`
register unconditionally (a failure there is fatal); every other row below
is a logged, nonfatal `try/except`:

| Surface | Module | Mount |
|---|---|---|
| Auth / sessions / pairing (user identity) | `api.auth_api` (`auth_bp`) | blueprint |
| Live usage reports | `api.usage_live` (`usage_live_bp`) | blueprint |
| Device workers mesh | `api.device_workers.routes` (`workers_bp`) | blueprint `/api/workers` |
| Dashboards | `api.dashboards.routes` | blueprint |
| Settings | `api.settings_routes` (`settings_bp`, url prefix `/api`) | blueprint — validation/persistence/defaults/authorization owned there; zero `/api/settings` routes remain on the root |
| Projects | `api.project_routes` (`projects_bp`, url prefix `/api`) | blueprint — transport; logic in `managers.project_manager` |
| Action forms | `api.action_form_routes` (`action_forms_bp`, url prefix `/api`) | blueprint — transport; logic in `api.action_forms` / `api.project_actions` |
| Git | `api.git_routes` (`git_bp`, url prefix `/api`) | blueprint — transport; behavior in `api.git_service` |
| Claude Code palette pins (`/api/claude/models\|model\|effort`) | `api.agent_harness.agents.claude.routes` (`claude_bp`) | blueprint — owned by the Claude agent slice; catalog in `agents/claude/model_catalog.py` |
| Chat widgets | `api.chat_widgets.register_chat_widget_routes` | `register_*(app)` |
| Chat TTS | `api.chat_tts` | `register_*(app)` |
| Web terminal | `api.web_terminal` | `register_*(app)` |
| Electron desktop | `api.desktop_electron` | `register_*(app)` |
| Android APK updates | `api.mobile_android_update` | `register_*(app)` |

JEV regress watcher is started as a side effect of importing this app (`api.jev.watch.ensure_started`).

---

## Chat-turn path (shared execution)

Inbound:

```
POST /api/chat (chat_endpoint)
  → normalize_chat_post (route-head validation), then auth
  → native /restart early-return (before sticky/starred prefixing, pre-pass)
  → supervised control lane (terminal return on match)
  → starred/sticky prefix, then attachment vision pre-pass
  → api.chat_turn selection + context (TurnSelection / build_turn_context)
  → api.chat_coordinator.submit_agent_turn (sync) /
    submit_agent_stream_turn (SSE)
       → harness / router-family / pipeline lanes
         (api.chat_turn_workflow; runners in api.agent_harness.runners)
  → persist (api.chat_turn_persist savers) + delivery tokens
    (api.chat_delivery) + action-form rewrite
```

`process_message_with_bot` is the compat entry for `/api/sessions/send` — it submits **unclaimed** through the same
coordinator; route agent lanes submit **claimed** with precomputed
selections. Owners never read the Flask request; `request_data` is captured
once at ingress. Empty messages select `pipeline`; nonempty unmatched
messages select `plain_router`, whose abstain also falls back to the
owned no-LLM outcome (`pipeline_fallback_result()`, never `None`) —
not graphs.

Siblings that **must stay consistent** with this path: `api.chat_turn`
(seam), `api.chat_turn_workflow` (lanes), `api.chat_turn_persist`
(savers), `api.chat_delivery` (turn tokens, pending results),
`api.chat_run_registry` (busy/cancel), `api.chat_live_status` /
`api.chat_status` (producer-owned status), `api.starred_slash`,
`api.vision_prepass`, `api.flask_restart`, `api.agent_harness.kernel`.

`POST /api/chat-steer` and `/api/chat-cancel` are part of the same turn machine, not generic CRUD.

Inbound Discord DM chat (`POST /api/pipeline-trigger-discord`) is **retired** (no such route on the root). Optional Discord REST agent-ops do not use this coordinator. Future surfaces should share one authenticated ingress (see [`../architecture/extension-boundaries.md`](../architecture/extension-boundaries.md)).

---

## HTTP still defined on `app` (by prefix)

Verified snapshot at HEAD: 139 `@app.route` decorators over 132 unique
paths (AST count). The clusters below are a grouping aid, not a second
exact inventory — recount from source before relying on completeness. Settings, projects, Git, and action-form HTTP
are **not** in this table — they live in their blueprints (zero root
routes each).

| Prefix / cluster | Prefer existing owner |
|---|---|
| `/api/router`, `/api/agent-router`, `/api/agents` | `api.agent_router` |
| `/api/sessions`, `/api/clear-session` | overlap with `auth_api` sessions |
| `/api/muse` `/api/hermes` `/api/codex` `/api/opencode` | harness catalog — avoid new per-agent forks |
| `/api/shared-media` | `api.shared_media` |
| `/api/shell` | pane layout (root-owned; agent read via `python -m api.panes_cli`) |
| `/api/chat` + steer/cancel/pending/live-status | `chat_turn*` / `chat_coordinator` / `chat_delivery` / `chat_run_registry` |
| `/api/flask/restart*`, `/api/restart`, `/api/status`, `/api/health` | `flask_restart`; health is **not** the daemon liveness probe (`/api/status` is) |
| `/api/local-llm/status`, `/api/ollama-models` | user-managed local server status and model discovery for router/completion providers |
| `/api/pairing` | user/DM pairing (not worker pairing) |
| `/api/cursor-agent`, `/api/project-commands` | harness catalog / `api.project_commands` |
| `/api/mobile`, `/api/supervised`, `/api/agent-context`, `/api/agent-defaults` | respective owners |
| `/api/start-launcher`, `/api/stop-launcher`, `/api/start-webapi`, `/api/stop-webapi`, `/api/start-discord`, `/api/stop-discord` | **410 stubs** via `_legacy_process_control_gone_response` — do not revive |
| HTML shells (`/`, `/chat_page.html`, Jobs, Dashboards, …) | static; fine to keep in the composition root |
| Static `/css` `/js` `/output` `/logs` `/sounds` | |

Worker **HTTP** is not in this table; it is `workers_bp`.

---

## Reverse dependencies (production `src/api`)

Exactly one production import, per the AST import scan
(`src/tests/quality/test_architecture_boundaries.py::test_no_reverse_imports_into_web_chat_api`,
`REVERSE_IMPORT_ALLOWLIST`; the scanner covers static and dynamic import
forms, and a stale allowlist entry fails the test): `api.doctor` imports
`api.web_chat_api` as an importability health probe (`try/except`, no
attribute use, reports `chat_backend` ok/fail in `/api/doctor`).
The only other permitted importer is the dev-only spawned-child
composition `src/scripts/cuttle_shadow_app.py` (never imported by an
owned layer; it imports the real app after installing deny guards).
Everything else — coordinators, runners, delivery, status services,
subagents, auth — imports its owner service directly. (Plain
comment/string mentions of the module name remain in a few headers and
guard regexes; those are not imports.)

Tests import `app` as a Flask `test_client` everywhere. That is expected.

**Limiter** lives in `api.limiter` so `auth_api` does not import this file at module top (comment in-file).

---

## Completed extractions (retain; do not re-do)

These seams are finished. Their absence from the Flask root is progress:

1. **Settings HTTP** — `api.settings_routes` (`settings_bp`); backed by `settings_manager`.
2. **Projects / Git** — `api.project_routes` / `api.git_routes` with their managers/services.
3. **Action-form HTTP** — `api.action_form_routes` over `action_forms` / `project_actions`.
4. **Live-status + status queues** — `api.chat_live_status` owns the store (root keeps a thin cancel-injecting wrapper); `api.chat_status` owns the legacy registry + `emit_status`, while turn-scoped `TurnStatusQueue` creation/drain/done-enqueue lives in `chat_turn_workflow.run_agent_stream_turn` (route only frames SSE).
5. **Harness web command entries** — `api.agent_harness.runners` owns all `run_*_web_command`; the root keeps a thin `_run_harness_web_command` shim.

What stays in the root until product work says otherwise: the Flask `app`,
HTML/static shells, `/api/chat` + `process_message_with_bot` ingress, SSE
pump framing, steer/cancel transport, restart/status/health, and the
remaining grab-bag product HTTP above. Do not extract “a thousand lines”
without a failing product reason and a dedicated PR series.

---

## Regression fence

Before large moves, run at least:

```bash
.venv/bin/python -m pytest \
  src/tests/auth/test_http_authz.py \
  src/tests/actions/test_action_forms.py \
  src/tests/actions/test_action_form_process_restart.py \
  src/tests/chat/test_agent_stop_then_followup.py \
  src/tests/chat/test_starred_agent_removal.py \
  src/tests/runtime/test_flask_restart.py \
  src/tests/workers/test_device_workers.py \
  -q
```

See `src/tests/README.md` for the offline gate (default collection,
`e2e` excluded) versus explicitly opt-in live/provider smokes — run the
offline gate for structural moves; live smokes are separate and never
required for docs validation. No paid prompt is needed to validate this
document.

## Retired developer pages

`test_reports.html`, its `/api/test-reports` listing, and the static `tools_page.html`
reference are removed. Developer reports belong in `src/tests/results/`; maintained
agent-operation guidance lives in `.cuttle_global/docs/agent-ops-cli.md`.
`query_log.html` is an active standalone wrapper around the shared query log
inspector and remains the target for report deep links and external-tab fallback.
