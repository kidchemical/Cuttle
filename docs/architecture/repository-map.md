# Cuttle architecture map (repository-wide)

**Date:** 2026-10-01 · **Baseline HEAD:** `4845233c` (original A1 audit snapshot) · Selected ownership rows maintained through later slices — not a claim that current source matches the baseline tree. Slice history lives in the [organization plan](ARCHITECTURE_ORGANIZATION_PLAN.md).
Prior snapshot: 2026-09-27 at `4f2880c`. Inventory review status: [`../reviews/repository-inventory.md`](../reviews/repository-inventory.md).

Companion: [`docs/guides/WEB_CHAT_API.md`](../guides/WEB_CHAT_API.md) (Flask composition root only), [`docs/guides/MODULARITY.md`](../guides/MODULARITY.md), this audit’s [`../reviews/repository-audit.md`](../reviews/repository-audit.md).

This map describes **what exists and how it boots**, not a mandate to extract `web_chat_api.py`.
Historical review documents under `docs/reviews/` are snapshots — they may
describe older trees and are preserved as-is, not corrected here.

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

**Flask-alone:** `python src/api/web_chat_api.py` from the repo root (same as the daemon child). Optional legacy test analytics on **:5000** now lives under `src/tests/reporting/` (`history_api.py`) — not the chat app.

**Startup listener audit** (one Flask `app`, three binds; full table in [`development-instance-safety.md`](development-instance-safety.md) §1): primary HTTPS **:8080** (`127.0.0.1` LAN-off, `0.0.0.0` LAN-on) owned by `web_chat_api.__main__`; companion HTTP **:8000** (same `app`, not a tombstone — CURRENT preferred Electron HTTP origin via `resolveUiBaseUrl` in `electron/main.js`, plus Android cleartext fallback) owned by `lan_access` constants + `__main__` socket setup; optional same-`app` phone TLS **:8888** only with a LAN IP. `lan_access` owns port constants and LAN bind/CORS/firewall; the daemon owns the Flask process and its :8080 health check, not socket setup — one app, multiple listener/server objects, no separate daemon-control listener. No per-listener routes/executors; `:5000` is a separate historical time-series listener.

**Dev-instance owner:** S1 shadow orchestration is `api.dev_instance` (parent side only — snapshots, allowlist env, owned child handle; never imports the Flask monolith) with the spawned child bootstrap in `src/scripts/cuttle_shadow_app.py` (dev-only composition, not an owned layer). Composition exception, narrow: alongside the `api.doctor` probe, the shadow bootstrap is the only other module permitted to import `web_chat_api`, and only inside the spawned child after deny guards — enforced by `REVERSE_IMPORT_ALLOWLIST` in `test_architecture_boundaries.py`. Runbook: [`development-instance-safety.md`](development-instance-safety.md). No generic `cuttle dev` command.

Removed since the prior snapshot: `src/scripts/launchers/` (four broken
launcher shims) and `src/launcher.py` (parallel bot+API launcher) no longer
exist. Do not follow old references to them.

---

## Process and HTTP composition root

Render completion media: `api.device_workers.render_results` owns durable
batch-to-chat bindings and staging/reconciliation. Worker store completion and
batch-watch writes invoke it; `AuthDatabase.add_message_once` owns atomic chat
delivery receipts. Runbook: [render-result-attachments.md](../../.cuttle/docs/render-result-attachments.md).

`src/api/web_chat_api.py` owns the `Flask app`, HTML routes, chat-turn HTTP,
process-control 410s, TLS helpers, and **registers**. Only the `try/except`
rows below are nonfatal (failure logged, boot continues); `auth_bp` and
`usage_live_bp` register unconditionally (a failure there is fatal):

| Blueprint / register | Package | Notes |
|---|---|---|
| `auth_bp` | `api.auth_api` | user identity, sessions, pairing, OAuth, guest |
| `usage_live_bp` | `api.usage_live` | live usage report API |
| `workers_bp` | `api.device_workers.routes` | mesh coordinator at `/api/workers` |
| `dashboards_bp` | `api.dashboards.routes` | dashboards catalog/service |
| `settings_bp` | `api.settings_routes` | `/api/settings/*`; validation/persistence/defaults/authorization owned there (video wallpaper prefs → `api.video_playlists`; wallpaper title/thumbnail proxy → `api.video_metadata`) |
| `projects_bp` | `api.project_routes` | transport; logic in `managers.project_manager` |
| `action_forms_bp` | `api.action_form_routes` | transport; logic in `api.action_forms` / `api.project_actions` |
| `git_bp` | `api.git_routes` | transport; behavior in `api.git_service` + git helpers |
| `tasks_bp` | `api.task_routes` | transport; logic in `managers.task_manager` |
| widgets, TTS, terminal, Electron, Android APK | `register_*_routes(app)` | `api.chat_widgets`, `api.chat_tts`, `api.web_terminal`, `api.desktop_electron`, `api.mobile_android_update` |

Zero `@app.route` entries for `/api/settings`, `/api/projects`,
`/api/git`, `/api/action-form`, or `/api/tasks` remain on the root file —
their absence is completed extraction work to retain, not a gap.

**Reverse imports:** exactly one production module imports the entry module —
`api.doctor` (importability health probe: `try/except` import, no attribute
use, reports `chat_backend` ok/fail). The only other permitted importer is
`src/scripts/cuttle_shadow_app.py`: a dev-only spawned-child composition,
not an owned layer and never imported by one (`api.dev_instance` spawns it
as a subprocess; it imports the real app only after installing deny
guards). Both entries are enforced by
`src/tests/test_architecture_boundaries.py` (`REVERSE_IMPORT_ALLOWLIST`,
AST scanner covering static and dynamic import forms; an allowlist entry
that stops resolving fails the test). The older “bottleneck” list of lazy importers (`chat_delivery`,
`auth_api`, `agent_router.dispatch`, `subagents`, kernel cwd) is obsolete —
the AST import scan finds no import of the entry module in any of them.
Plain comment/string mentions remain (e.g. `limiter.py`,
`chat_turn_persist.py`, `git_service.py` headers; process-guard regexes) —
those are not imports.

Daemon liveness is `GET /api/status`. `GET /api/health` is a heavier diagnostic (CLI probes) — do not confuse them.

---

## Chat turn (web)

```
User (web)
  → POST /api/chat (chat_endpoint)
  → normalize_chat_post (route-head validation), then auth
  → native /restart early-return (before sticky prefixing, pre-pass)
  → supervised control lane (terminal return on match)
  → starred/sticky prefix, then attachment vision pre-pass
  → api.chat_turn selection + context (TurnSelection / build_turn_context)
  → api.chat_coordinator.submit_agent_turn (sync) /
    submit_agent_stream_turn (SSE)
  → api.chat_turn_workflow lanes (harness, router-family, pipeline)
  → api.chat_turn_persist savers + api.chat_delivery tokens
  → SSE pump + action-form rewrite
```

**“Pipeline” today** names the active no-LLM fallback, not graphs:
`select_agent_turn` returns a `pipeline` selection for empty messages;
a nonempty unmatched message selects `plain_router`, whose abstain
(`None`) also falls back to the owned no-LLM outcome.
`pipeline_fallback_result()` (never `None`) is that fallback;
the sync pipeline lane runs `run_pipeline_sync_turn` itself while the stream
pipeline lane submits through the coordinator. Graph-era HTTP and the node
editor are retired; `retired_pipeline_registry` is an always-empty shape
stub. Do not delete pipeline code by keyword.

**Unified saver policy (B1, integrated):** both pipeline savers route
through the shared `make_assistant_saver`, so cancellation/system rows
(`[CANCELLED]`, `ui == 'system'`) never become assistant history while
transport completion and user cancellation feedback are preserved
independently. Pinned by the updated
`test_oracle_pipeline_stream_cancelled_row_divergence` and
`test_chat_persistence_policy.py`; logged as `[ERR-20261001-001]`
(Fixed). Do not reintroduce a lane-local persist-anything saver.

**Discord inbound chat gateway:** retired (2026-09). See [`extension-boundaries.md`](extension-boundaries.md) and [`../reviews/discord-cleanup-2026-09.md`](../reviews/discord-cleanup-2026-09.md).

**Discord channel read/post (keep; no gateway required):** `python -m api.discord_cli` (REST GET) and `discord.post` (REST POST via `api.discord_ops` / `project_actions`). Token from `src/.env` does not start a gateway. Per-project aliases: `{project}/.cuttle/actions/discord-post.yaml`. Arbitrary snowflakes cannot bypass the post allowlist.

**Live turn:** `api.chat_delivery` (turn tokens `current_turn` /
`is_stale_turn`, `try_begin`/`end`, `is_turn_cancelled`,
`cancel_current_turn`) + `api.chat_run_registry` (busy/cancel) +
`/api/chat-steer` / `/api/chat-cancel` (same turn machine, not generic
CRUD). A superseded run cannot persist, park, unlock, or re-route.

**Live status:** `api.chat_live_status` owns the process-lifetime
cross-device status store (get/set/clear/active ids, TTL eviction);
`web_chat_api` keeps thin wrappers that inject the delivery cancel
predicate at the composition root. Delivery, auth, run-registry, and
supervised orchestration import the service — never the Flask entry module.
Restart wipes the store (chats default idle until live-status confirms);
never move it into per-request objects.

**Status queues:** `api.chat_status` owns the legacy process-lifetime
session→transport-queue registry (`_QUEUES`) plus `emit_status`, which
fans one status line out to live publish + queue put and — inside
`turn_status_scope` — redirects legacy session-keyed emitters into the
producer's actual turn queue. Emit requires an explicit cancel predicate
and publish callback per call. Turn-scoped producer fanout is separate:
`chat_turn_workflow.run_agent_stream_turn` creates the `TurnStatusQueue`,
the worker publishes/finishes/finalizes and enqueues `("done", result)`
inside the scope, and the skeleton drains events into semantic yields;
the route only frames/adapts those yields into SSE. A subscriber that
detaches before done does not release the running worker (skeleton
`finally`). `api.chat_status_phases` is a leaf formatter taking an
injected `emit_fn`, never importing the entry module.

**Metadata shapers + chat cwd:** `api.chat_metadata` owns the
message/badge shaping service (`muse_model_label`,
`usage_meta_from_assistant_result`, `user_badge_metadata` — no module
state, no Flask routes, no persistence writes, no delivery
orchestration). It is not pure: per-agent session stores and starred
defaults are read through lazy leaf imports (module header).
`managers.project_manager` owns `default_chat_cwd(pm, root)` with
explicit inputs plus `REPO_ROOT`. `web_chat_api` keeps thin aliases.
`internal_http` is loopback-HTTP-only.

**Turn envelope + selection:** `api.chat_turn` owns the transport-neutral
turn seam — `TurnRequest`/`normalize_chat_post` (route-head validation),
`TurnSelection`/`classify_selection` (restart → harness → router with
injected matchers), `split_db_session_id`, `build_turn_context`. The
`/api/chat` route and `process_message_with_bot` delegate to it.
`process_message_with_bot` is the compat entry for local-mode prompts and
`/api/sessions/send`.

**Turn workflow:** `api.chat_turn_workflow` owns lane orchestration —
`run_agent_sync_turn` (harness + router-family sync lanes),
`run_pipeline_sync_turn` (leftover-pipeline sync lane with stale/cancel
no-persist), `run_agent_stream_turn` (stream lane with live publish/clear),
`finalize_stream_result` (stream completion tail: save → notify → park →
release), `build_pipeline_body`, `busy_response_body`,
`is_turn_superseded`. Deps are narrow callables plus the `chat_delivery`
service; Flask serialize/SSE transport stays in the route.
`api.agent_harness.runners` owns all `run_*_web_command` entries.

**History:** `api.auth_db` SQLite (`src/data/db/`, gitignored). Pairing store for **users** is separate from **worker** enroll.

---

## Agent subsystem

| Piece | Location |
|---|---|
| Catalog / adapters | `src/api/agent_harness/agents/*` |
| Kernel | `src/api/agent_harness/kernel.py` |
| Router | `src/api/agent_router/` |
| CLI wrappers | `src/scripts/utilities/*_cli_tool.py` |
| Codex thread writer ownership | `src/api/agent_harness/codex_thread_ownership.py` — process-local leases shared by app-server turns, exec/resume, context probes, and compaction; `test_codex_thread_ownership.py` + `test_codex_ownership_handoff.py`; process teardown stays in `scripts.utilities.agent_process` |
| Project commands/actions | `{project}/.cuttle/` + global `.cuttle_global/` |
| Brain / context compile | `src/api/cuttle_brain/` |

Graph-era **HTTP** `POST /api/execute-tool` and `_execute_remote_agent_tool` are fully removed. Execution is `api.agent_harness.runners` → `kernel.run_agent_web_command`; turn orchestration is `api.chat_turn_workflow`, persistence `api.chat_turn_persist`.

**Turn persistence:** `api.chat_turn_persist` owns `make_assistant_saver` (skip guards: supervised-owned rows, empty failures, `[CANCELLED]`, `ui == 'system'`, cancelled turns), `persist_user_turn` (badge/history/project merge), and `persist_auth_user_message`. All take explicit `db` + `request_data` (captured once at ingress) — no Flask reads inside. The entry wrappers only inject project/metadata/titler shapers. Stream-thread saves merge the captured body instead of an empty re-read.

**Application coordinator:** `api.chat_coordinator` is the
transport-neutral turn entry — `PreparedAgentTurn` (frozen plain data),
`AgentSelection` (`router_family` | `plain_router` | `harness` |
`mode_blocked` | `harness_empty_prompt` | `pipeline`),
`select_agent_turn` (one decision tree), `AgentTurnIO` (sync surface arms,
all required) / `StreamTurnIO` (stream surface arms),
`submit_agent_turn` / `submit_agent_stream_turn` (shared
claimed/unclaimed execution; pipeline fallback never `None`). Route agent
lanes submit claimed with precomputed selections; `process_message_with_bot`
(local prompts, `/api/sessions/send`) submits unclaimed with plain bodies
and a naked fallback. No-LLM control lanes, session resolve, SSE pump, and
`jsonify` stay ingress-side. Owners never read the Flask request;
`request_data` is captured once at the boundary and never mutated
(pinned by test).

**BYO-CLI boundary:** `api.agent_harness.installer`
(executable machinery: npm installs, remote-script download+execute,
PATH refresh) is deleted; the kernel missing-CLI path answers with
manifest guidance and never shells out. The 4 auto-install manifests
(antigravity, deepseek, opencode, claude) are guidance-only: install
recipes and auto flags removed, hints reworded to self-install.
Discovery, availability/version validation, invocation, capability
normalization, and `install_hint`/`missing_cli_hint` rendering
(frontend palette reads hints only) are unchanged.
**Project-adapter trust:** project drop-ins stay
opt-in (`CUTTLE_ALLOW_PROJECT_ADAPTERS`); manifest id/slash validated
before import (folder name canonical, hijack slashes rejected); each
external adapter loads once (mtime-keyed) as a uniquely-namespaced
package with no `sys.path` mutation; only relative sibling imports
resolve, on demand; bare absolute imports fail loudly; purge lifetime pinned.
Trust posture: opted-in project code is trusted unsandboxed, no sandbox claims.

---

## Workers mesh

Coordinator HTTP: `/api/workers/*` (`device_workers`). Store: gitignored SQLite via `CUTTLE_DEVICE_WORKERS_DB` / default path. Enroll is LAN/RFC1918 + setting (see GitHub #1). Runtime claim/complete still loopback-friendly.

---

## AuthZ

`api.http_authz` (owner / session / UI operator). `api.auth_api` sessions, OAuth, guest. Worker tokens in `device_workers.auth`. Action forms HMAC in `project_actions`.

---

## Settings

HTTP is extracted: `settings_bp` (`api.settings_routes`, url prefix
`/api`) owns validation/persistence/defaults/authorization; app defaults
live in `src/managers/settings_manager.py`; `managers.settings_storage` owns
locked atomic persistence. `src/settings.json` owns server preferences,
`src/data/config/machine_settings.json` owns `device_workers`/`discovery`, and
`src/data/config/ui_state.json` owns `ui_layout`/`shell_workspaces` (all gitignored).
Existing unversioned settings stay monolithic until the guarded cold-start migration;
`schema_version: 1` records the split, independently of the release SemVer.
Retired graph settings, node-editor preferences, graph sandbox, and inbound Discord
channel config are removed. Zero `/api/settings` routes remain on the Flask root.

**Model/runtime preference store:** `core.config.RuntimeConfig` owns
`src/data/config/runtime_config.json`, resolved by `core.runtime_paths.runtime_config_path`
independently of cwd. `BotConfig` and `bot_config_path` remain compatibility aliases.
Existing `src/bot_config.json` remains authoritative until the guarded offline
migration; the repo-root legacy copy is never read or merged. Missing reads keep
defaults in memory; explicit saves create the parent and preserve unknown keys.
Live consumers are `api.settings_routes`, `api.query_tracker`, and `core.local_llm`.

GitHub App credentials and verified commit identity belong to `api.github_app`.
`settings_routes` owns the Account settings HTTP surface; Cuttle Git commits
consume the shared identity through `git_service.stage_and_commit` and
`scripts.utilities.git_pending_changes`. See `.cuttle/docs/github-app.md`.
Runtime/model preferences remain separate from SettingsManager's scoped stores;
these stores are not merged. Browser `localStorage` is still device-specific;
saved server UI state is still install-wide, not newly account-scoped. `src/bot.py` and its obsolete gateway-owner tests are removed.

---

## Persistence

Runtime storage layout is owned by `core.runtime_paths`: `src/data/db/`,
`sessions/`, `brain/`, `supervised_tasks/`, `edit_attribution/`, `cache/`, and
`home_automation/`. Existing `workspace/` and root-level home automation JSON stay
authoritative until `core.runtime_data` migrates them offline. The daemon invokes
the guarded migration before starting services on its next cold launch; a live
host or destination conflict skips migration. Details: `src/data/README.md`.
Install-local harness packs belong in `.cuttle_global/personal/agents/`; legacy
`src/data/harness_agents/` discovery remains compatible until offline migration.


| Store | Tracked? |
|---|---|
| `cuttle_auth.db` chat/auth | ignored |
| HMAC secret file | ignored |
| Certs | ignored |
| Pairing JSON | likely ignored `*.json` |
| Query logs / uploads | `src/output`, `src/web/logs` ignored or generated |

---

## Frontend

Vanilla JS: `src/web/js/app_shell.js` (shell), `chat_page.js` (chat). No bundler. Electron loads the same app, preferred HTTP `:8000` with HTTPS `:8080` fallback (`resolveUiBaseUrl` in `electron/main.js`).

**Chat frontend ownership:** `chat_page.js` is the page orchestrator — it
owns DOM, transport URLs, timer handles, and persistence effects.
Decisions/state and bounded transport/controller effects have explicit owners (loaded before the page;
owners never touch page globals — capabilities arrive as explicit
arguments or injected host interfaces):

| Owner | Owns | Page keeps (effects) |
|---|---|---|
| `chat_turn_guard.js` (`CuttleTurnGuard`) | turn/staleness generation tokens (pure) | bump/capture call sites |
| `chat_stop_state.js` (`CuttleStopState`) | stop/cancel flags + abort classification (pure) | notices, transport abort, dispatch |
| `chat_followup_queue.js` (`CuttleFollowupQueue`) | follow-up queue state + take/reconcile, composes `chat_activity.js` items (pure) | drain timer, persistence, edit UI |
| `chat_subagent_fleet.js` (`CuttleChatSubagentFleet`) | experimental fleet-card markup for child chats (pure; outcome/summary from `api.subagents.fleet`); `ChildStatusSink` persists coalesced, turn-guarded live status in the child SQLite row | mounting, click → open chat, live polling and history hydration |
| `chat_pending_result.js` (`CuttleChatPendingResult`) | SSE event classification, pending-result waiter, history-recovery match, exactly-once sync classification, stale-heal, send-failure recovery (pure decisions; async flows take transport/paint/clock as `deps`) | fetch/paint/clock, session-adopt guards, timers, busy lock |
| `chat_stream.js` (`CuttleChatStream`) | SSE byte transport: `readEvents(body, {holdMs, readTimeoutMs, signal?, onEvents})` owns the native reader, one retained read promise across timeout observations, read timers, TextDecoder + LF-double-newline framing + JSON decode, reader cancel on hold detach/abort, and lock release on terminal/eof/error | fetch request, HTTP status/content-type/JSON fallback, event classification, session adoption, DOM paint, sawProgress, final-result mapping, turn/Stop/busy ownership, pending recovery, debug messages |
| `pending_changes_panel.js` (`CuttlePendingChangesPanel`) | standalone periodic scans and explicit panel refresh/actions; embedded panes use existing shell per-project polling hub | `app_shell.js` owns periodic shared scans; page project reconciliation reports path through `reportProjectToShell`; no duplicate embedded interval |
| `chat_generation.js` (`CuttleChatGeneration`) | busy-lock `{loading, localSessionId, seq}` + token-scoped release, sync cadence, detached-poll classes, session-open flags; one sync-claim state `{inFlight, startedAt, seq}` through `createSyncState`, `claimSync`, `isSyncCurrent`, `finishSync` | timers, transport, voice, running-flag paint |
| `chat_messages.js` (`CuttleChatMessages`) | records/windowing, display dispatch as pure functions | transcript DOM paint, sync/poll, session restore, streaming orchestration, leaf renderers |
| `chat_action_forms.js` (`CuttleChatActionForms`) | pure card model/watch interpretation (`isExplicitActionFormCancelOption`, `actionFormHasSideEffect`, `isWatchFormAction`, `cardWatchBind`, `normalizeWatchBars` formatting transforms, restart-link helpers) plus card HTML/render planning (`renderActionFormCardHtml`, `renderWatchBarsHtml`, job-agnostic `renderWatchGridHtml` — no render target, no DOM) | card DOM/button/watch wiring moved to `chat_action_cards` (`CuttleChatActionCards`, plan C2 done), never into this pure module |
| `chat_markdown.js` | markdown/block composition (pure) | page render orchestration |
| `chat_slash.js` (`CuttleChatSlash.SLASH_COMMANDS`) | client slash registry incl. control commands | palette UI, dispatch |
| `chat_activate.js` (`CuttleChatActivate`) | four container-scoped activation helpers with injected `deps`: `activateVegaEmbeds`, `attachCodeCopyButtons`, `highlightCodeBlocks`, `wireTerminalInputs` — **not** an action-card mount controller | page `activateEnhancements` orchestration (non-card helpers), re-arm timers; card effects moved to `chat_action_cards` |
| `chat_action_cards.js` (`CuttleChatActionCards`) | card-effects controller: `mountCards(root, host)` validates an explicit chat root plus required host capabilities (throws otherwise) and composes one instance per root; card mount/submission/dismissal, lock/progress DOM effects, watch/restart loops, choice storage, adoption and linked-restart recovery; one explicit lifetime record per mount (signal-bound listeners, abortable fetches, cancellable waits, token-guarded continuations), one root-scoped observer plus dispose/destroy lifecycle (moves within root preserved, removal equals explicit disposal, re-mount installs a fresh lifetime); restart scans scoped to the owning root, discord followup coalesced through one controller-owned in-flight write per logical followup with the storage ack recorded only after confirmed server persistence (coalescing holds within the controller lifetime only; cross-reload delivery stays ambiguous — no durable pre-request claim, server-side idempotency separately owned) | send lanes, history panel, composer, message transport, session/project/auth context (all arrive as explicit host capabilities incl. deferred storage triple; never page scope) |
| `chat_usage_live.js` (`CuttleUsageLive`) | `render(text, format)` is pure; `createBroker(host)` owns coalesced fetch + refresh timers via `host`, but `start(format)` reads `root.document`/`window` directly (`MutationObserver`, scroll/visibility/resize listeners, `innerHTML` paint) and caches the broker on `host.__cuttleUsageLiveBroker` — the page does not own its scheduler | `usage_live_bp` API (`api.usage_live`) |

Pure today: `chat_action_forms` model/watch interpretation + card HTML planning,
`chat_messages`/`chat_markdown` planning, turn/stop/queue/generation
decisions. Effects live in `chat_activate` helpers, the
`chat_action_cards` controller, `chat_usage_live.start`, and the page —
do not describe all frontend modules as pure.

**Experimental feature UI:** `api.experimental` owns rollout flags, and Settings
→ Experimental renders only generic flag controls. Feature workflows own their
surfaces: Achievements uses `/achievements_page.html` (page blueprint in
`api.achievements.routes`, `achievements_page.js` presentation), registered as a
stashed App in the shell. New features generally start experimental; surface
selection and exceptions are documented in `.cuttle/docs/experimental-features.md`.

**Chat VFX:** `api.chat_vfx` owns validated transient confetti/toast events and
session-scoped SQLite delivery (`python -m api.chat_vfx`); `chat_vfx.routes`
owns authenticated HTTP transport. `chat_vfx.js` owns the renderer and a
polling controller with injected session/transport/timer capabilities;
`chat_page.js` composes its page lifetime. The achievement presenter delegates
confetti to this renderer, while keeping durable unlock/ack delivery separate.
VFX is independent of experimental flags. Runbook: `.cuttle_global/docs/chat-vfx.md`.

**Completion notifications (experimental):** `completion_notifications.js` owns
the browser-local completion broker, permission/privacy controls, and delivery
deduplication. The shell shares one broker with its panes; `chat_page.js` reports
turn lifecycle events and `chat_action_cards.js` reports newly observed watch
terminal transitions through an injected callback. General settings owns the
permission workflow; Experimental owns only the rollout flag. No background
push service is added. Runbook: `.cuttle/docs/completion-notifications.md`.

**Spaces:** `src/web/js/spaces/` (`CuttleSpaces.*`: state, groups, order,
activity, drop); the shell owns the singleton, DOM, frames, and
persistence effects.

**Pane tree (not yet separated):** `snapshotLayoutTree` serializes the
layout from the live DOM while reading shell maps (`columnState`,
`lastChatByColumn`, `pageWithPaneSession`); `flattenLayoutLeaves` is
nearby in `app_shell.js`. There is no standalone pure pane-tree module —
E1 kept this boundary after task-local review and real layout fences; E2 was skipped without a demonstrated extraction benefit.

Streaming lifecycle fixes belong in the lifecycle/state owners plus
page adapters/tests — not in unrelated page regions. Deferred, still
page-side by design: message-sync/history timer mechanics, session-adopt
nav guards, running-flag store/paint. Byte framing/reader cleanup belongs
to `chat_stream`; sync claim/replacement/release belongs to `chat_generation`.

---

## Navigability baseline (current regions, state, gates)

Recorded pre-move at the A1 baseline (`4845233c`) and maintained
since, so later slices can compare working context against that commit.
Identifiers below are current guidance, not an untouched snapshot —
card render/effects, config path, and stream fixes have since moved to
their owners, while pane layout and page-side sync/transport effects
remain where they were. Nothing here is a proposed interface.

| Task | Regions / symbols | Mutable state + interfaces touched | Tests |
|---|---|---|---|
| Action-card render | page `formatMessage` + `actionFormBlocks` placeholder array + narrow `renderActionFormCard` adapter (pre-rendered preview, explicit session/esc in; all in `chat_page.js`); card HTML/render planning `CuttleChatActionForms.renderActionFormCardHtml` (`chat_action_forms.js`, pure no-DOM); page `activateEnhancements` (non-card helpers only) + narrow adapters (`dismissOpenInteractiveCards`, `runningWatchJobIds`, `formAwaitingSessionIdFromCard`, cross-pane listener) delegating to the card-effects controller; card mount/submission/dismissal, lock/progress DOM, watch/restart loops, choice storage, adoption and linked-restart recovery in `CuttleChatActionCards.mountCards` (`chat_action_cards.js`, one instance per chat root, explicit host capabilities); backend `rewrite_action_forms`, `merge_qa_resume_specs` (`action_forms.py`), `prepare_assistant_text_for_actions` (`project_actions.py`) | card lock/selection attrs, watch timers, HMAC-signed persisted specs, one-writer answer bubble (`sendMessage` only) | `test_chat_action_forms.py`, `test_action_forms.py`, `test_action_form_routes.py`, `test_chat_action_card_render.py` (Node render battery, 16), `test_chat_action_cards.py` (Node controller contract, 3), `e2e/test_chat_action_card_render.py` (isolated browser, 3), `e2e/test_chat_action_cards_controller.py` (isolated browser, 7), `e2e/test_chat_action_card_effects.py` (23 lifecycle/submission/delivery cases) |
| History sync / recovery | page timers `messageSyncTimer`, `startMessageSync`, `stopMessageSync`, `scheduleNextMessageSync`, `syncSessionMessagesFromServer` (all in `chat_page.js`); `recoverChatResult`, `recoverChatResultWithRetries`, per-message sync classification (`chat_pending_result.js`); generation tokens `createGenerationState`/`beginGeneration`/`endGeneration` and sync claim transitions `createSyncState`/`claimSync`/`isSyncCurrent`/`finishSync` (`chat_generation.js`); byte reads `CuttleChatStream.readEvents` (`chat_stream.js`); backend `finalize_stream_result` (`chat_turn_workflow.py`), `make_assistant_saver` skip guards (`chat_turn_persist.py`), `current_turn`/`is_stale_turn`/`is_turn_cancelled` (`chat_delivery.py`) | busy lock, one sync-claim state (replacement invalidates old finishers), existing page navigation sequence (A→B→A fence), sync cursor/timers, turn tokens, pending-result store, assistant-row skip guards; saver takes explicit `db` + captured `request_data` | `test_chat_turn_persist.py`, `test_chat_turn_workflow.py`, `test_chat_coordinator_acceptance.py`, P5-E/P5-F oracles, `test_stop_refresh_live_status.py`, `test_chat_stream.py`, `test_chat_generation.py`, `e2e/test_chat_stream_reader.py`, `e2e/test_chat_sync_lifetime.py`, `e2e/test_shadow_chat_stop_resend.py` |
| Pane layout | `snapshotLayoutTree`, `flattenLayoutLeaves`, `pageWithPaneSession`; shell maps `columnState`, `lastChatByColumn`; `CuttleSpaces.*` (`src/web/js/spaces/`); backend `_shell_panes_snapshot`, `GET/POST /api/shell/panes`, `.../panes/<n>/messages` (all in `web_chat_api.py`); agent read `python -m api.panes_cli` | `columnState`, `lastChatByColumn`, saved-layout shape (restore-compatible); leaf/group normalization mixed with DOM reads | `test_shell_panes.py`, `test_shell_workspaces.py`, `test_pane_space_drag.py`, `test_panes_cli.py`; isolated `e2e/test_app_shell_layout.py` (6: nested restore/flex, real close, pointer focus, Spaces/reload and v1 compatibility; shell kept as-is, see `docs/reviews/pane-layout-fences.md`) |
| Pending-change polling | `reconcileChatProject` → `reportProjectToShell` (`chat_page.js`); `CuttlePendingChangesPanel.create` (`pending_changes_panel.js`); existing `pollPendingChangesHub` / `refreshPendingChangesPath` (`app_shell.js`) | shell column→project map, per-path in-flight coalescing; standalone-only panel interval; explicit refresh messages retained | `e2e/test_pending_changes_polling.py`, `e2e/test_shared_diff_modal.py`; opt-in `e2e/test_chat_cost_profile.py` |
| Config | HTTP `settings_routes.py` (`settings_bp`; bot/model branch via `get_config()`); defaults `get_settings_manager()` (`settings_manager.py`); `RuntimeConfig.load_config`/`save_config` (`core/config.py`) — file defaults to `src/data/config/runtime_config.json` via `runtime_paths.runtime_config_path()` (legacy `src/bot_config.json` retained until migration) (never cwd); path conventions `core/runtime_paths.py` | shell settings vs runtime/model preferences (two stores kept; legacy `src/` copy remains authoritative until migration, repo-root copy untouched, no auto-merge); unknown-key preservation on save | `test_settings_routes.py`, `test_runtime_paths.py`, `test_runtime_config_paths.py` |

---

## CLI / automation

Git push diagnostics: `.cuttle/scripts/git-hooks/pre-push` owns project policy;
`core.git_push_diagnostics` owns per-commit/SQLite inspection and redacted reports;
`api.git_routes` composes HTTP responses; `CuttleGitPushReport` owns the shared
report popup and delegates explicit commit-file views to `CuttleDiffModal`.


Canonical: `python -m api.*` (`.cuttle_global/docs/agent-ops-cli.md`). Global actions: `.cuttle_global/actions/*.yaml`. POSIX launchers: `.cuttle/scripts/*.sh` + `.cuttle_global/scripts/*.sh`. Windows `.bat`/`.ps1` still present for mixed hosts.

---

## Legacy / superseded (see audit)

Graph JSON pipelines and the node editor are **retired**. Jobs live path: `/api/cuttle-jobs` + workers. Graph `fetch` leftovers remain in `jobs_page.html` / `job_insight.html` until stream 1. Telegram/Slack HTTP is **unwanted**. Discord REST read/post does **not** use a gateway process. Extension map: [`extension-boundaries.md`](extension-boundaries.md). Historical consumer tables: [`../reviews/graph-discord-consumers.md`](../reviews/graph-discord-consumers.md).

Still present (verified): port-5000 time-series stack
(`src/tests/reporting/start_history_server.py`), control panel HTML. Removed (G1, proved
unrouted — explicit per-file routes only, no HTML wildcard, no startup/test
consumer): `landing_page_backup.html` (historical mentions in reviews stay as
history). Gone (prior map references corrected above):
`src/scripts/launchers/`, `src/launcher.py`. “Pipeline” still names the
active no-LLM fallback: do not delete it by keyword. Keep Discord REST
operations, active execution tracking, query tracking, and live BotConfig
consumers.

---

### Cuttle Development

Before making non-trivial changes, read:
- `docs/architecture/ARCHITECTURE_PRINCIPLES.md`
- `docs/architecture/repository-map.md`

For harnesses, integrations, surfaces, or agent operations, also read:
- `docs/architecture/extension-boundaries.md`

---

## Important invariants

- Never kill Flask from an agent it hosts; use daemon `/restart`.
- Action-form recovery: HMAC on persisted specs (`action_forms` / `project_actions`).
- `project_key = "pc_bot"` still documented as Claude Code project root in `AGENTS.md`.
- Electron sandbox: no automatic `--no-sandbox`.
