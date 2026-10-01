# Cuttle architecture map (repository-wide)

**Date:** 2026-09-27 (continued) · **HEAD:** `4f2880c` · **Code:** not modified. Inventory review status: [`../reviews/repository-inventory.md`](../reviews/repository-inventory.md).

Companion: [`docs/guides/WEB_CHAT_API.md`](../guides/WEB_CHAT_API.md) (Flask composition root only), [`docs/guides/MODULARITY.md`](../guides/MODULARITY.md), this audit’s [`../reviews/repository-audit.md`](../reviews/repository-audit.md).

This map describes **what exists and how it boots**, not a mandate to extract `web_chat_api.py`.

---

## Boot sequence

```
start_cuttle.sh | src/scripts/cuttle_daemon.py
  → load src/.env
  → spawn Flask: python src/api/web_chat_api.py  (HTTPS :8080)
  → tray / cron / local worker loop (platform-dependent)
  → Flask restart: daemon reads cuttle_flask_restart_request.json (not agent taskkill)
```

**Electron Host:** `.cuttle/scripts/launch-cuttle-host.sh` → `electron/main.js` `--mode=host` (Chromium sandbox via `electron-sandbox.sh`). Host talks to local Flask.

**Electron Client:** LAN thin client; enrolls as a **device worker** (`electron/device-worker/cuttle_device_worker.py` + `src/scripts/cuttle_device_worker.py`).

**Android:** `apps/mobile` Capacitor shell; `apps/android_companion` / `apps/android_bt_voice` are additional native surfaces.

**Flask-alone:** `python src/api/web_chat_api.py` (repo root; same as the daemon child). Do not use `src/scripts/start_api_server.py` (different Flask on **:5000**, `time_series_api.py`).

**Do not use** `src/scripts/launchers/start_web_chat.py` (or the other three files in that folder): they `chdir`/`sys.path` to `launchers/` and import `web_chat_api.py` / `project_manager` from the wrong directory. **Confirmed broken** by source, not by running.

**Legacy parallel launcher:** `src/launcher.py` still starts bot+API **from `src/`** (`api/web_chat_api.py`) without the daemon restart protocol — overlapping, not the supported path.

---

## Process and HTTP composition root

`src/api/web_chat_api.py` owns `Flask app`, HTML routes, chat-turn HTTP, many leftover graph/process routes, TLS helper, and **registers**:

| Blueprint / register | Package |
|---|---|
| `auth_bp` | `api.auth_api` |
| `workers_bp` | `api.device_workers.routes` |
| `dashboards_bp` | `api.dashboards.routes` |
| widgets, TTS, terminal, Electron, Android APK | `register_*_routes(app)` |

**Bottleneck:** anything that needs live status, harness cwd, or assistant metadata often **lazy-imports** `web_chat_api` (`chat_delivery`, `auth_api`, `agent_router.dispatch`, `subagents`, `kernel._default_chat_cwd`).

Daemon liveness is `GET /api/status`. `GET /api/health` is a heavier diagnostic (CLI probes) — do not confuse them.

---

## Chat turn (web)

```
User (web)
  → POST /api/chat
  → sticky/starred slash, vision pre-pass
  → native /restart
  → process_message_with_bot → harness kernel / agent router
  → SSE + persist + action-form rewrite
```

**Discord inbound chat gateway:** retired (2026-09). See [`extension-boundaries.md`](extension-boundaries.md) and [`../reviews/discord-cleanup-2026-09.md`](../reviews/discord-cleanup-2026-09.md).

**Discord channel read/post (keep; no gateway required):** `python -m api.discord_cli` (REST GET) and `discord.post` (REST POST via `api.discord_ops` / `project_actions`). Token from `src/.env` does not start a gateway. Per-project aliases: `{project}/.cuttle/actions/discord-post.yaml`. Arbitrary snowflakes cannot bypass the post allowlist.

**Live turn:** `api.chat_delivery` + `api.chat_run_registry` + `/api/chat-steer` / `/api/chat-cancel`.

**Live status (Phase 4 P4-1):** `api.chat_live_status` owns the
process-lifetime cross-device status store (get/set/clear/active ids,
TTL eviction); `web_chat_api` keeps thin wrappers that inject the
delivery cancel predicate at the composition root. Delivery, auth,
run-registry, and supervised orchestration import the service — never
the Flask entry module. Restart wipes the store (chats default idle
until live-status confirms); never move it into per-request objects.

**Status queues (Phase 4 P4-2):** `api.chat_status` owns the
process-lifetime session→transport-queue registry plus the emit fanout
(live publish + `('status', msg)` put, full-queue drop). Emit requires
an explicit cancel predicate and publish callback per call — no silent
unguarded defaults. `web_chat_api` keeps a thin wrapper injecting the
turn-cancelled guard; queue creation/drain, the `('done', result)`
completion put, and the SSE pump loop stay in transport/route code.
`api.chat_status_phases` is a leaf formatter taking an injected
`emit_fn`, never importing the entry module.

**Metadata shapers + chat cwd (Phase 4 P4-3):** `api.chat_metadata`
owns the pure message/badge shapers (`muse_model_label`,
`usage_meta_from_assistant_result`, `user_badge_metadata` — no module
state, `typing`-only imports); `managers.project_manager`
owns `default_chat_cwd(pm, root)` with explicit inputs plus
`REPO_ROOT`. `web_chat_api` keeps thin aliases. `internal_http`
is loopback-HTTP-only (dead in-process `test_client` POST deleted).
Remaining entry-module imports are the Phase-5 runner calls
(`agent_router.dispatch`, `supervised/adapters`) plus a `doctor`
smoke probe — no helper reverse imports left.

**Turn envelope + selection (Phase 5 P5-A):** `api.chat_turn` owns the
transport-neutral turn seam — `TurnRequest`/`normalize_chat_post`
(route-head validation), `TurnSelection`/`classify_selection`
(restart → harness → router with injected matchers), `split_db_session_id`,
`build_turn_context`. The `/api/chat` route and
`process_message_with_bot` delegate to it; execution, persistence, and
delivery stay in the entry module for P5-B. Runner reverse imports
(`dispatch`, `supervised/adapters`) intentionally untouched until the
core workflow moves.

**Turn workflow (Phase 5 P5-B):** `api.chat_turn_workflow` owns the
lane orchestration — `run_agent_sync_turn` (harness + router-family
sync lanes), `run_pipeline_sync_turn` (leftover-pipeline sync lane with
stale/cancel no-persist), `finalize_stream_result` (stream completion
tail: save → notify → park → release), `build_pipeline_body`,
`busy_response_body`, `is_turn_superseded`. Deps are narrow callables
plus the `chat_delivery` service; Flask serialize/SSE transport stays
in the route. `api.agent_harness.runners` owns all seven
`run_*_web_command` entries — the router imports the owner, and the
only remaining entry-module import in production is the `doctor`
smoke probe.

**History:** `api.auth_db` SQLite (`src/data/db/`, gitignored). Pairing store for **users** is separate from **worker** enroll.

---

## Agent subsystem

| Piece | Location |
|---|---|
| Catalog / adapters | `src/api/agent_harness/agents/*` |
| Kernel | `src/api/agent_harness/kernel.py` |
| Router | `src/api/agent_router/` |
| CLI wrappers | `src/scripts/utilities/*_cli_tool.py` |
| Project commands/actions | `{project}/.cuttle/` + global `.cuttle_global/` |
| Brain / context compile | `src/api/cuttle_brain/` |

Graph-era **HTTP** `POST /api/execute-tool` and `_execute_remote_agent_tool` are fully removed (no references in `src/`; this paragraph previously claimed otherwise). Execution is `api.agent_harness.runners` → `kernel.run_agent_web_command`; turn orchestration is `api.chat_turn_workflow`, persistence `api.chat_turn_persist`.

**Turn persistence (Phase 5 P5-C):** `api.chat_turn_persist` owns `make_assistant_saver` (skip guards verbatim: supervised-owned rows, empty failures, `[CANCELLED]`, `ui == 'system'`, cancelled turns), `persist_user_turn` (badge/history/project merge), and `persist_auth_user_message`. All take explicit `db` + `request_data` (captured once at ingress) — no Flask reads inside. The entry wrappers only inject project/metadata/titler shapers. Stream-thread saves now merge the captured body instead of an empty re-read (pinned by the threaded SSE stamp test, which fails on the pre-capture tree).

**Application coordinator (Phase 5 P5-D):** `api.chat_coordinator` is the
transport-neutral turn entry — `PreparedAgentTurn` (frozen plain data),
`select_agent_turn` (one decision tree: router-family → harness /
mode-block / empty-prompt → plain-router → pipeline), `AgentTurnIO`
(surface arm implementations, all required), `submit_agent_turn`
(shared claimed/unclaimed execution; shortcuts formatted by the
surface; pipeline entry stays per-surface). Route agent lanes submit
claimed with precomputed selections; `process_message_with_bot` (local
prompts, `/api/sessions/send`) submits unclaimed with plain bodies and
a naked fallback. No-LLM control lanes, session resolve, SSE pump, and
`jsonify` stay ingress-side. Owners never read the Flask request;
`request_data` is captured once at the boundary and never mutated
(pinned by test).

**BYO-CLI boundary (Phase 6 P6-A):** `api.agent_harness.installer`
(executable machinery: npm installs, remote-script download+execute,
PATH refresh) is deleted; the kernel missing-CLI path answers with
manifest guidance and never shells out. The 4 auto-install manifests
(antigravity, deepseek, opencode, claude) are guidance-only: install
recipes and auto flags removed, hints reworded to self-install.
Discovery, availability/version validation, invocation, capability
normalization, and `install_hint`/`missing_cli_hint` rendering
(frontend palette reads hints only) are unchanged. Trust posture
unchanged and still narrow: project drop-ins stay opt-in
(`CUTTLE_ALLOW_PROJECT_ADAPTERS`), bundled-only execution was already
refused, `sys.path` append-only. Next slices: project-adapter trust
audit (approval/manifest/import isolation) and surface/agent-ops
boundary review.

---

## Workers mesh

Coordinator HTTP: `/api/workers/*` (`device_workers`). Store: gitignored SQLite via `CUTTLE_DEVICE_WORKERS_DB` / default path. Enroll is LAN/RFC1918 + setting (see GitHub #1). Runtime claim/complete still loopback-friendly.

---

## AuthZ

`api.http_authz` (owner / session / UI operator). `api.auth_api` sessions, OAuth, guest. Worker tokens in `device_workers.auth`. Action forms HMAC in `project_actions`.

---

## Settings

`src/managers/settings_manager.py` → `src/settings.json` (**gitignored** `*.json`). Defaults created if absent. HTTP `/api/settings/*` still on `web_chat_api`. `bot_config.json` duplicated at repo root, `src/`, `electron/`, tests — `src/core/config.py` looks for `bot_config.json` relative cwd (**uncertain** which copy wins).

---

## Persistence

| Store | Tracked? |
|---|---|
| `cuttle_auth.db` chat/auth | ignored |
| HMAC secret file | ignored |
| Certs | ignored |
| Pairing JSON | likely ignored `*.json` |
| Query logs / uploads | `src/output`, `src/web/logs` ignored or generated |

---

## Frontend

Vanilla JS: `src/web/js/app_shell.js` (shell), `chat_page.js` (chat). No bundler. Electron loads the same HTTPS origin.

**Chat frontend ownership (Phase 3 closure):** `chat_page.js` is the
page orchestrator — it owns DOM, transport URLs, timer handles, and
persistence effects. Decisions/state live in one owner module each
(loaded before the page; no reverse dependencies — owners never touch
`document`/`window`/`fetch`/timers):

| Owner | Owns (decisions/state) | Page keeps (effects) |
|---|---|---|
| `chat_turn_guard.js` | turn/staleness generation tokens | bump/capture call sites |
| `chat_stop_state.js` | stop/cancel flags + abort classification | notices, transport abort, dispatch |
| `chat_followup_queue.js` | follow-up queue state + take/reconcile (composes `chat_activity.js` items) | drain timer, persistence, edit UI |
| `chat_pending_result.js` | pending-result waiter, history-recovery match, sync exactly-once classification, stale-heal, SSE event classes, send-failure recovery, restart poll | fetch/paint/clock, session-adopt guards |
| `chat_generation.js` | busy-lock `{loading, localSessionId, seq}` + token-scoped release, sync cadence, detached-poll classes, session-open flags | timers, transport, voice, running-flag paint |
| `chat_messages.js` etc. (8A–8F) | records/windowing, display formatting, markdown/activation, prompt history | render orchestration, streaming |

Streaming lifecycle fixes belong in the lifecycle/state owners plus
page adapters/tests — not in unrelated page regions. Deferred, still
page-side by design: message-sync/history timer mechanics, SSE byte
transport, session-adopt nav guards, running-flag store/paint.

---

## CLI / automation

Canonical: `python -m api.*` (`.cuttle_global/docs/agent-ops-cli.md`). Global actions: `.cuttle_global/actions/*.yaml`. POSIX launchers: `.cuttle/scripts/*.sh` + `.cuttle_global/scripts/*.sh`. Windows `.bat`/`.ps1` still present for mixed hosts.

---

## Legacy / superseded (see audit)

Graph JSON pipelines and the node editor are **retired**. Jobs live path: `/api/cuttle-jobs` + workers. Graph `fetch` leftovers remain in `jobs_page.html` / `job_insight.html` until stream 1. Telegram/Slack HTTP is **unwanted**. Discord REST read/post does **not** use a gateway process. Extension map: [`extension-boundaries.md`](extension-boundaries.md). Historical consumer tables: [`../reviews/graph-discord-consumers.md`](../reviews/graph-discord-consumers.md).

`src/launcher.py` vs daemon. Port-5000 time-series stack. `landing_page_backup.html`. Control panel HTML still linked.

---

## Important invariants

- Never kill Flask from an agent it hosts; use daemon `/restart`.
- Action-form recovery: HMAC on persisted specs (`action_forms` / `project_actions`).
- `project_key = "pc_bot"` still documented as Claude Code project root in `AGENTS.md`.
- Electron sandbox: no automatic `--no-sandbox`.
