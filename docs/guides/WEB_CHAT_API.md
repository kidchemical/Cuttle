# `web_chat_api.py` dependency map

**Status:** living architecture reference. **Do not treat this file as a license to extract code.**  
**File:** `src/api/web_chat_api.py` (~15,060 lines, ~234 `@app.route` handlers as of 2026-09-27).  
**Audience:** agents and humans about to change Flask/chat/settings/workers.

Cuttle’s Flask **app object** lives here. Many product surfaces already have their own modules; this file is still the **composition root** (blueprints, HTML, chat-turn HTTP, leftover pipeline 410s).

When you add a backend feature:

1. Read this map and prefer an existing package (`api.agent_harness`, `api.device_workers`, `api.auth_api`, `api.dashboards`, …).
2. Do **not** add a new import from `web_chat_api` into a leaf module if a helper can live next to its owner.
3. Do **not** start a structural extraction unless product work is blocked (regressions, duplicated routes, or the file is slowing the change). Extraction is a separate initiative, not a prerequisite for building Workers/GUI/dashboards.

Related: [`MODULARITY.md`](MODULARITY.md), [`docs/ROADMAP.md`](../ROADMAP.md), security residuals in [`docs/reviews/security-hardening-2026-09.md`](../reviews/security-hardening-2026-09.md).

Tracked (do not implement in the same breath as product features):

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
| Chat-turn coordinator | `POST /api/chat`, `process_message_with_bot`, SSE stream, persist/rewrite |
| Process / LAN / restart HTTP | `/api/flask/restart*`, `/api/status`, LAN settings, 410 process-control stubs |
| Grab bag of product HTTP | Git UI API, projects, tasks, Govee, action-forms, harness pin APIs, pipeline 410s |

**Daemon contract:** `cuttle_daemon` spawns this module as the Flask child on port **8080**. Coordinated restart is `flask_restart` / `/restart` — never `taskkill` the API from an agent it hosts.

---

## Already extracted (mounted from here)

Registered near the top of `web_chat_api.py` (import failures are logged, not fatal):

| Surface | Module | Mount |
|---|---|---|
| Auth / sessions / pairing (user identity) | `api.auth_api` (`auth_bp`) | blueprint |
| Device workers mesh | `api.device_workers.routes` (`workers_bp`) | blueprint `/api/workers` |
| Dashboards | `api.dashboards.routes` | blueprint |
| Chat widgets | `api.chat_widgets.register_chat_widget_routes` | `register_*(app)` |
| Chat TTS | `api.chat_tts` | `register_*(app)` |
| Web terminal | `api.web_terminal` | `register_*(app)` |
| Electron desktop | `api.desktop_electron` | `register_*(app)` |
| Android APK updates | `api.mobile_android_update` | `register_*(app)` |

JEV regress watcher is started as a side effect of importing this app (`api.jev.watch.ensure_started`).

---

## Chat-turn path (the coordinator)

Inbound:

```
POST /api/chat
  → starred/sticky prefix, attachment vision pre-pass
  → native /restart (before router)
  → process_message_with_bot
       → local-LLM launch gate
       → harness slash match → _run_harness_web_command / _run_pinned_harness_turn
       → agent router (api.agent_router)
       → fallback
  → SSE (_generate_chat_stream) + persist (_make_auth_assistant_saver)
  → prepare_assistant_text_for_actions (confirms / action forms)
```

Inbound Discord DM chat (`POST /api/pipeline-trigger-discord`) is **retired**. Optional Discord REST agent-ops do not use this coordinator. Future surfaces should share one authenticated ingress (see [`../architecture/extension-boundaries.md`](../architecture/extension-boundaries.md)).

Siblings that **must stay consistent** with this path: `api.chat_delivery` (turn tokens, pending results), `api.chat_run_registry` (busy/cancel), `api.starred_slash`, `api.vision_prepass`, `api.flask_restart`, `api.agent_harness.kernel`.

`POST /api/chat-steer` and `/api/chat-cancel` are part of the same turn machine, not generic CRUD.

---

## HTTP still defined on `app` (by prefix)

Counts are `@app.route` entries on this file only (blueprints extra).

| Prefix / cluster | ~N | Prefer existing owner |
|---|---|---|
| `/api/settings`, `/api/app-settings`, `/api/pipeline-settings` | 19+ | `settings_manager` / future settings blueprint |
| `/api/git`, Ungit HTML | 19+ | git UI; keep restart-safe |
| `/api/projects` | 10 | project chip / `project_manager` |
| `/api/home-automation` | 9 | `managers.home_automation` |
| `/api/tasks` | 8 | tasks widget vs this HTTP |
| `/api/router`, `/api/agent-router`, `/api/agents` | 5+ | `api.agent_router` |
| `/api/action-form` | 4 | `api.action_forms` (logic already there) |
| `/api/muse` `/api/hermes` `/api/codex` `/api/opencode` `/api/cursor-agent` | 4 each / 1 | harness catalog — avoid new per-agent forks |
| `/api/sessions`, `/api/clear-session` | 4+ | overlap with `auth_api` sessions |
| `/api/shell` | 4 | pane layout (`api.panes` / shell) |
| `/api/chat*` live status, pending, cancel, steer | ~8 | `chat_delivery` / `chat_run_registry` |
| `/api/flask/restart*`, `/api/restart`, `/api/status`, `/api/health` | ~6 | `flask_restart`; health is **not** the daemon liveness probe (`/api/status` is) |
| `/api/local-llm` | 3 | llama.cpp launch |
| `/api/pairing` | 3 | user/DM pairing (not worker pairing) |
| `/api/pipeline-*` and start/stop process | many | **410 stubs** or leftover graph bookkeeping — do not revive graphs |
| HTML shells (`/`, `/chat_page.html`, Jobs, Dashboards, …) | ~24 | static; fine to keep in the composition root |
| Static `/css` `/js` `/output` `/logs` | ~7 | |

Worker **HTTP** is not in this table; it is `workers_bp`.

---

## Reverse dependencies (production `src/api`)

These import `web_chat_api` (often **lazy**, to avoid import cycles). Adding more of these makes extraction harder.

| Importer | Symbols / why |
|---|---|
| `agent_router/dispatch.py` | `_run_harness_web_command` as router runners |
| `agent_router/supervised/adapters.py` | harness web commands |
| `agent_router/supervised/orchestrator.py` | `set_chat_live_status` |
| `agent_harness/kernel.py` | `_default_chat_cwd` |
| `auth_api.py` | `active_live_session_ids` (busy chats for session list) |
| `chat_delivery.py` | `get_chat_live_status`, `clear_chat_live_status` |
| `chat_run_registry.py` | `clear_chat_live_status` |
| `chat_status_phases.py` | `emit_chat_status` |
| `subagents/turns.py` | `_assistant_message_metadata` |
| `subagents/identity.py` | `_user_badge_metadata` |
| `internal_http.py` | app for in-process HTTP |
| `doctor.py` | import app as a sanity load |

Tests import `app` as a Flask `test_client` everywhere. That is expected.

**Limiter** lives in `api.limiter` so `auth_api` does not import this file at module top (comment in-file).

---

## Natural extraction seams (when it actually pays)

Order if you ever extract — **preserve routes, JSON shapes, and Flask restart**:

1. **Settings HTTP** (`/api/settings/*`) — already backed by `settings_manager`; low coupling to turns.
2. **Git / projects / Govee / tasks** — feature islands with their own managers.
3. **Action-form HTTP** — thin wrappers over `action_forms` / `project_actions`.
4. **Live-status helpers** — move `set/get/clear_chat_live_status` next to `chat_delivery` so supervised/auth stop importing this module.
5. **Harness web command shims** (`_run_harness_web_command`) — already thin; router should depend on `agent_harness`, not this file.
6. **Chat-turn coordinator last** — `chat_endpoint` + `process_message_with_bot` + SSE. Highest risk to daemon restart.

Do not extract “a thousand lines” without a failing product reason and a dedicated PR series.

---

## Regression fence

Before large moves, run at least:

```bash
.venv/bin/python -m pytest \
  src/tests/test_http_authz.py \
  src/tests/test_action_forms.py \
  src/tests/test_action_form_process_restart.py \
  src/tests/test_agent_stop_then_followup.py \
  src/tests/test_starred_agent_removal.py \
  src/tests/test_flask_restart.py \
  src/tests/test_device_workers.py \
  -q
```

Full `src/tests/` plus a live `/cursor` turn after Flask restart is the product check, not this document.
