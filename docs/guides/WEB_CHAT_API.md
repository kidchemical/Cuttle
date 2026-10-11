# `web_chat_api.py` dependency map

**Status:** living architecture reference. **Do not treat this file as a license to extract code.**
**File:** `src/api/web_chat_api.py` (~5,100 lines; 53 `@app.route`
decorators over 52 unique paths, counted from the AST at 2026-10-11,
branch `refactor/work`). The monolith refactor (Phases 1–6) moved the
chat page, shell and backend route families into owners; the ceiling gate
`src/tests/quality/test_composition_root_growth.py` now pins 5,106 lines.
**Audience:** agents and humans about to change Flask/chat/settings/workers.

Cuttle’s Flask **app object** lives here. Many product surfaces already have their own modules; this file is still the **composition root** (blueprints, HTML, chat-turn HTTP).

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
| Flask `app` factory-in-place | TLS bootstrap (cert owner `api.tls_cert`), CORS-ish headers, rate limiter, static/HTML routes |
| Chat-turn HTTP ingress | `POST /api/chat`, `process_message_with_bot` (compat), SSE stream, steer/cancel; decisions in `chat_turn` / `chat_coordinator` / `chat_turn_workflow` |
| Restart / status HTTP | `/api/flask/restart*`, `/api/status`, `/api/health` |
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
| Experimental flags | `api.experimental.routes` (`experimental_bp`, url prefix `/api/experimental`) | blueprint — generic flag toggles |
| Achievements | `api.achievements.routes` (`achievements_bp`, url prefix `/api/achievements`, plus `achievements_pages_bp` page) | blueprints — first experimental feature |
| Agent events / Feed | `api.agent_events.routes` (`bp`, no url prefix: `/api/agent-events`, `/agent_feed.html`) | blueprint — owner-only fleet log transport |
| Gizmos | `api.gizmos.routes` (`gizmos_bp`, url prefix `/api/gizmos`, plus `gizmos_pages_bp` page) | blueprints — experimental live UI objects |
| Chat VFX | `api.chat_vfx.routes` (`chat_vfx_bp`, url prefix `/api/chat-vfx`) | blueprint — transient confetti/toast events |
| Projects | `api.project_routes` (`projects_bp`, url prefix `/api`, plus `projects_pages_bp` page) | blueprints — transport; logic in `managers.project_manager` |
| Action forms | `api.action_form_routes` (`action_forms_bp`, url prefix `/api`) | blueprint — transport; logic in `api.action_forms` / `api.project_actions` |
| Git | `api.git_routes` (`git_bp`, url prefix `/api`) | blueprint — transport; behavior in `api.git_service` |
| Claude Code palette pins (`/api/claude/models\|model\|effort`) | `api.agent_harness.agents.claude.routes` (`claude_bp`, url prefix `/api/claude`) | blueprint — owned by the Claude agent slice; catalog in `agents/claude/model_catalog.py` |
| Provider palette pins (`/api/muse\|/api/hermes\|/api/codex\|/api/opencode/{models,model,effort}`) | `api.agent_harness.agents.<id>.routes` (`muse_bp`/`hermes_bp`/`codex_bp`/`opencode_bp`, matching url prefixes) | blueprints — one per agent slice; contract identical to Claude's |
| Cursor Agent models (`/api/cursor-agent/models`) | `api.agent_harness.agents.cursor.routes` (`cursor_bp`, url prefix `/api/cursor-agent`) | blueprint — owned by the Cursor agent slice |
| Router / agent-router options | `api.agent_router.routes` (`agent_router_bp`) | blueprint — `/api/router/*`, `/api/agent-router/options` |
| Router editor | `api.agent_router.routes` (`router_editor_bp`, url prefix `/api/router`) | blueprint — decision-only tools |
| Supervised task controls | `api.agent_router.supervised.routes` (`supervised_bp`) | blueprint — `/api/supervised/tasks/*` transport; logic in `supervised.orchestrator`/`store` |
| Agent catalog / defaults / context | `api.agent_harness.routes` (`agent_harness_bp`) | blueprint — `/api/agents`, `/api/agent-defaults/*`, `/api/agent-context[/compact]` |
| Provider API keys | `api.settings_routes` (`provider_keys_bp`) | blueprint — `/api/{test,save}-api-key`, `/api/load-api-keys`; owned with the settings/provider surface |
| Flask restart | `api.flask_restart` (`flask_restart_bp`) | blueprint — `/api/flask/restart*` (daemon-mediated) |
| Shared media staging | `api.shared_media` (`shared_media_bp`) | blueprint — `/api/shared-media/*` |
| Mobile companion events | `api.mobile_companion` (`mobile_companion_bp`) | blueprint — `/api/mobile/*` |
| User pairing | `api.auth_api` (`pairing_bp`) | blueprint — `/api/pairing/*` |
| Activity stream | `api.activity_stream` (`activity_bp`, url prefix `/api`) | blueprint — chat activity push stream |
| Tasks gizmos | `api.gizmos.tasks_routes.tasks_bp` | scoped routes under `/api/gizmos/tasks` |
| Chat TTS | `api.chat_tts` | `register_*(app)` |
| Voice narrator (experimental `voice_narrator`) | `api.voice_narrator.routes` (`voice_narrator_bp`) | blueprint `/api/voice-narrator` — line writing + voicing owned by `api.voice_narrator` |
| Voice transcription (experimental `voice_server_stt`) | `api.voice_stt.routes` (`voice_stt_bp`) | blueprint `/api/voice-stt` — per-phrase OpenAI transcription owned by `api.voice_stt` |
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

Verified snapshot at HEAD: 99 `@app.route` decorators over 92 unique
paths (AST count). The clusters below are a grouping aid, not a second
exact inventory — recount from source before relying on completeness. Settings, projects, Git, and action-form HTTP
are **not** in this table — they live in their blueprints (zero root
routes each).

| Prefix / cluster | Prefer existing owner |
|---|---|
| `/api/sessions`, `/api/clear-session` | overlap with `auth_api` sessions |
| `/api/shell` | pane layout (root-owned; agent read via `python -m api.panes_cli`) |
| `/api/chat` + steer/cancel/pending/live-status | `chat_turn*` / `chat_coordinator` / `chat_delivery` / `chat_run_registry` |
| `/api/status`, `/api/health` | daemon liveness is `/api/status`; health is **not** the liveness probe |
| `/api/local-llm/status`, `/api/ollama-models` | user-managed local server status and model discovery for router/completion providers |
| `/api/project-commands` | `api.project_commands` |
| HTML shells (`/`, `/chat_page.html`, Jobs, Dashboards, …) | static; fine to keep in the composition root |
| Static `/css` `/js` `/output` `/logs` `/sounds` | |

Moved off the root into blueprints (Phases 6A/6C — do **not** re-add): `/api/router` + `/api/agent-router` (`agent_router_bp`), `/api/agents` + `/api/agent-defaults` + `/api/agent-context` (`agent_harness_bp`), `/api/{muse,hermes,codex,opencode}/{models,model,effort}` + `/api/cursor-agent/models` (`agents/<id>/routes.py`), `/api/flask/restart*` (`flask_restart_bp`), `/api/shared-media` (`shared_media_bp`), `/api/mobile` (`mobile_companion_bp`), `/api/supervised` (`supervised_bp`), `/api/pairing` (`pairing_bp`), `/api/{test,save}-api-key` + `/api/load-api-keys` (`provider_keys_bp`). Their absence is enforced structurally by `src/tests/quality/test_architecture_boundaries.py::test_phase7_route_families_delegate_to_owners`.

Retired: the legacy `/api/start-*` / `/api/stop-*` process-control endpoints are fully removed — no routes and no stubs remain on the root. Do not revive them.

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
6. **Route families (Phase 6A)** — router/agent-router (`agent_router_bp`), agent catalog/defaults/context (`agent_harness_bp`), provider API keys (`provider_keys_bp`), Flask restart (`flask_restart_bp`), shared media (`shared_media_bp`), mobile events (`mobile_companion_bp`), supervised controls (`supervised_bp`), user pairing (`pairing_bp`).
7. **Provider model/effort HTTP (Phase 6C)** — `/api/{muse,hermes,codex,opencode}/{models,model,effort}` and `/api/cursor-agent/models` live in `api.agent_harness.agents.<id>.routes` (one blueprint per agent slice), matching the existing Claude `claude_bp`.
8. **`chat_endpoint` lanes (Phase 6B)** — supervised control, launch gates, shell-action expansion, `/help`, router-config, route/retry, harness and pipeline lanes moved to `api.chat_turn_workflow`; the route keeps request capture, auth, busy SSE/409, SSE framing and `jsonify`.
9. **Chat-turn owners (Phase 4)** — `api.chat_coordinator` (shared submit), `api.chat_turn_workflow` (lane orchestration), `api.chat_turn_persist` (savers), `api.chat_turn` (seam); the root composes them.

What stays in the root until product work says otherwise: the Flask `app`,
HTML/static shells, `/api/chat` + `process_message_with_bot` ingress, SSE
pump framing, steer/cancel transport, status/health, and the
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
