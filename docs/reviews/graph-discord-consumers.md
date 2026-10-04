# Graph, Telegram/Slack, and Discord consumer trace

**Status (2026-09-27):** Stream **1c Discord gateway is complete**. This file is a **historical investigation** from before deletion. Current architecture: [`../architecture/extension-boundaries.md`](../architecture/extension-boundaries.md). Cleanup report: [`discord-cleanup-2026-09.md`](discord-cleanup-2026-09.md). Inventory rows that still name `src/bots/discord_bot.py` are stale until the next inventory regen.

**Date:** 2026-09-27 · originally written against HEAD `4f2880c`.

Product decisions for this pass (operator):

- Graph pipelines and the node editor are **retired** (not a Jobs product fork).
- Preserve **current** Jobs (`/api/cuttle-jobs` + workers mesh + `/api/executing-jobs`), workers, and **web** agent chat.
- **Remove** Telegram and Slack routes, settings, and references.
- **Keep** Discord **channel read** (`python -m api.discord_cli`) and **approved posts** (`discord.post` / `project_actions._execute_discord_post`). That matches global [`.cuttle_global/docs/discord.md`](../../.cuttle_global/docs/discord.md). Per-project aliases live in `{project}/.cuttle/actions/discord-post.yaml` (Escape Purgatory Feature Updates uses that path; this Cuttle workspace did not load EP rules).
- **Investigate** retiring Discord **DM / guild AI chat** and the **daemon `discord_bot.py` process**. **Done** — see [`discord-cleanup-2026-09.md`](discord-cleanup-2026-09.md).

Evidence is grep + route/handler reads, not a production traffic dump.

---

## Discord: two independent planes

| Plane | Mechanism | Needs gateway process `bots/discord_bot.py`? | Keep if DM chat is retired? |
|---|---|---|---|
| Channel **read** | `src/api/discord_cli/cli.py` → Discord REST `GET` with `Bot` token | **No** | **Yes** |
| Channel **post** | `discord.post` → `_execute_discord_post` in `project_actions.py` → REST `POST` | **No** | **Yes** |
| Token load | `_load_discord_bot_token()` (`DISCORD_TOKEN` / `DISCORD_BOT_TOKEN` in `src/.env`) | No | **Yes** |
| DM / guild **AI chat** | Gateway `discord.Client` in `discord_bot.py` → `POST /api/pipeline-trigger-discord` → `process_message_with_bot` + `discord_chat_bridge` | **Yes** | **No** (this is the retirement candidate) |
| Shell “Discord connected” | `~/cuttle_logs/discord_status.json` written only by `discord_bot._write_discord_status` | Yes (today) | Rewrite or drop; Flask `/api/status` reads that file |
| `import discord` (discord.py) | `discord_bot.py`, `launcher.py` probe, test runners | Gateway / install check | Library optional if only REST remains (`requests` already used) |

Hub runbook still says “Never force-kill the Discord bot” because the **daemon still spawns it**. If the gateway is retired, that sentence should change to “do not kill Flask; REST posts do not need the gateway process.”

---

## “Bot” files — consumers before any delete

| Path | Runtime consumers | Tests / docs | Classification |
|---|---|---|---|
| `src/bots/discord_bot.py` | Daemon `start_discord_bot()` if `DISCORD_TOKEN` set; `src/launcher.py` `start_discord_bot`; `launcher_debug.py` (broken cwd) | `test_discord_integration.py` (string + helpers); e2e `pipeline-trigger-discord` | **Gateway AI chat.** Only required for DM/mention → Flask. **Not** on the read/post plane. |
| `src/bots/__init__.py` | Package for `bots.discord_bot` | — | Keep while `discord_bot.py` exists |
| `src/bot.py` | **None in production** | `test_security.py`, `test_username_case.py` (`is_owner` / `OWNER_ID`) | Test shim. Keep until those tests move to `discord_bot` or `OWNER_ID` env |
| `src/api/discord_chat_bridge.py` | `web_chat_api` Discord trigger + `/invite` handling ~6158, ~7043 | `test_discord_chat_bridge.py` | **DM/guild CH sessions.** Retire with gateway chat. `chat_page.js` “Invite Discord” depends on this |
| `src/api/discord_cli/` | Agents / humans: `python -m api.discord_cli` | `test_discord_cli.py` | **Keep** (read plane) |
| `src/api/project_actions.py` (`discord.post`) | Action-form click → Flask | many `test_*action*` | **Keep** (post plane) |
| `bot_config.json` (5 copies) | `core.config.BotConfig` `Path("bot_config.json")` cwd-relative; `discord_bot` and `query_tracker` / `web_chat_api` call `get_config()` | unit security tests | **Not** Discord REST. LLM-mode JSON. Trace cwd before deleting copies; **not** tied to gateway vs REST |
| `src/scripts/utilities/kill_bots.py` | **Broken** (`from launcher` with `utilities/` on path); Electron README points at missing `src/kill_bots.py` | — | Abandoned; delete with launcher stream |
| `bot_mcp.py` | **File does not exist.** Strings in `launcher.py`, `cuttle_managed_process_guard.py` | integration test mentions `bot_mcp` | Stale identifier only |

---

## Graph-era HTTP and UI (retired product)

Preserve: `/api/cuttle-jobs`, `/api/workers/*`, `/api/executing-jobs` (`app_shell.js` heartbeat), `active_executions` (kernel), `query_tracker` (kernel).

### Jobs UI (must not lose workers / Gitea jobs)

| Client | Call | Server today | After graph removal |
|---|---|---|---|
| `jobs_page.html` `loadCuttleJobs` | `GET /api/cuttle-jobs` | Live | **Keep** |
| `jobs_page.html` `loadDevices` / cancel | `/api/workers/...` | Live | **Keep** |
| `jobs_page.html` `loadPipelines` | sets `allJobs = []` | No fetch | Graph browse is already empty; **delete** browse/render/start/stop/runNow/editPipeline JS + “No pipelines” tab if unused |
| `jobs_page.html` `startJob` / `stopJob` / `runNow` / schedule toggle | `/api/pipeline-start` etc. | 410 | Dead if browse list empty; **remove JS** so Jobs cannot depend on 410 |
| `jobs_page.html` links | `/job_insight.html?pipeline=` | Page served | Graph insight; **remove or stub** after confirming no worker job uses that query |
| `job_insight.html` | `GET /api/job-insight`, pipeline-run-now/start/stop/schedule | Live graph JSON 404 / 410 | **Remove** with graphs |
| `app_shell.js` | `GET /api/executing-jobs` | `active_executions` | **Keep** (harness runs) |

### Routes to tombstone/delete (no remaining wanted caller)

Callers traced in `src/web` except as noted.

| Route | JS/HTML caller | Other caller | Notes |
|---|---|---|---|
| `POST /api/pipeline-run-now` | jobs + job_insight | — | Already 410 |
| `POST /api/pipeline-start/stop/schedule-toggle/register-running` | jobs + job_insight | — | 410 |
| `POST /api/pipeline-reload` | none found | unreachable body | Early return |
| `GET /api/jobs` | none found (Jobs uses cuttle-jobs) | dead tail after empty return | |
| `GET /api/job-insight` | job_insight.html | — | Live body, graph files missing |
| `POST /api/pipeline-job-status` / `pipeline-check-running` | none in `src/web` | empty `running_pipelines` | |
| `POST /api/pipeline-execution-finish` | `src/scripts/create_test_execution.py` only | large live body | **Not** kernel; kernel uses `query_tracker` in-process |
| `POST /api/record-node-execution` | `create_test_execution.py`; `force_reload.js` (unreferenced file) | — | |
| `POST /api/execute-tool` / `execute-output` | **no** `src/web` | tests string-assert helper exists | In-process helper still used **only** from `execute_tool()` |
| `POST /api/save-pipeline` etc. | none | 410 | |
| `GET /api/list-pipelines` | none | empty list | |
| `GET/POST /api/pipeline-settings/*` | **no** `src/web` | settings_manager defaults | Graph defaults |
| `GET /api/pipeline-chats` | uncertain | leftover pipeline chat files | Trace `app_shell` before delete |
| `POST /api/pipeline-trigger-schedule` | — | 410 | |
| Telegram/Slack trigger routes | **no** `src/web` | `_handle_external_trigger` | **Remove** (unwanted), not “keep because executable” |
| `POST /api/pipeline-trigger-discord` | **only** `discord_bot.py` | e2e test | Keep **until** gateway chat retired; then delete with bot process |
| `GET /node_editor.html` `/pipeline_chat.html` | redirects | tray `on_node_editor` | Keep redirects or retarget tray only |
| `src/web/js/force_reload.js` | **no HTML includes it** | looks for `PipelineExecutor` | Abandoned graph cache buster |
| `src/api/sandbox_policy.py` | **no production import** | unit tests only | Graph **tool-node** evaluator. Separate from HTTP settings sandbox. Optional delete with graphs **only** if still unreferenced. |
| `/api/settings/sandbox` | **Keep out of graph removal.** See below. | Flask + `test_http_authz.py` + GitHub #4 | **Live settings/authz API**, not “dead because no HTML fetch” |

### `/api/settings/sandbox` (not graph-era)

Do **not** infer death from missing `src/web` `fetch`. This route is part of the owner/guest settings surface reviewed in [`security-hardening-2026-09.md`](security-hardening-2026-09.md) and listed with LAN GET as GitHub [#4](https://github.com/kidchemical/Cuttle/issues/4).

| Method | Auth | Consumers | Role |
|---|---|---|---|
| `POST /api/settings/sandbox` | `require_owner()` (~8666) | `test_anonymous_sandbox_post_rejected` (no session → 401), `test_guest_cannot_change_sandbox` (guest cookie → 403) | High-risk **mutate** of `settings.json` `sandbox` (enabled, allow/deny tool lists, session-kind restrict, `allowed_pipelines`) |
| `GET /api/settings/sandbox` | **None** today | Same Flask app; any LAN/loopback client; **no** first-party HTML caller found | **Read** of that block. Security review: still public — **deferred**, not unused |

`SettingsManager.get_sandbox_config` / `set_sandbox_config` are only called from these handlers (plus `set_` initializing the dict). Kernel / `agent_harness` / `process_message_with_bot` do **not** read this config today. Cursor `/sandbox` in `chat_page.js` is **CLI `--sandbox enabled\|disabled`** (`cursor_agent_commands.py`), a different mechanism. Electron `chrome-sandbox` is a third.

**Enforcement gap (not a delete ticket):** POST can persist policy that nothing in the harness currently evaluates. That is a product/security follow-up, not proof the HTTP API is graph leftover.

**Graph removal:** keep GET/POST and `settings_manager` sandbox accessors. Telegram/Slack **names inside** `restrict_for_session_kinds` can be dropped in stream 1b without deleting the API. `allowed_pipelines` is graph-shaped; leave the field until #4 / a real enforcer is designed — do not rip the route in stream 1.

**`sandbox_policy.py`:** still unused in production (evaluates graph `tool-*` node types). Deleting **that module** is separable from deleting **`/api/settings/sandbox`**.

### Must not delete with graphs

- `process_message_with_bot` / `/api/chat`
- `_run_harness_web_command` / `agent_harness.kernel`
- `active_executions` / `query_tracker` / `query_events`
- `vision_prepass` (web uploads; also used on Discord trigger)
- Workers + cuttle-jobs + `job_watch`
- **`GET`/`POST /api/settings/sandbox`** and `SettingsManager` sandbox accessors (authz + #4; see above)

---

## Telegram / Slack (remove)

| Location | What |
|---|---|
| `web_chat_api.py` `telegram_trigger_endpoint` / `slack_trigger_endpoint` / `_handle_external_trigger` | Only used by those two routes (Discord has its own handler) |
| `settings_manager` `channels.telegram` / `channels.slack` | Defaults |
| `GET/POST /api/settings/channels` loops `telegram`, `slack` | No `src/web` fetch found; still API surface |
| sandbox `restrict_for_session_kinds` `telegram_user`, `slack_user` | Defaults |
| `auth_db.py` comment “later Telegram” | Comment |
| `landing_page.html` Slack marketing bullets | Copy only |

No Telegram/Slack bot process exists in the daemon.

---

## Discord DM/guild AI chat — retirement investigation (not executed)

If retired, delete or stop spawning:

1. Daemon `start_discord_bot` + restart-loop around Discord PID  
2. `src/bots/discord_bot.py`  
3. `POST /api/pipeline-trigger-discord` and Discord-only branches in that handler  
4. `discord_chat_bridge.py` + tests  
5. Pairing checks for channel `discord` on **inbound** chat (webchat pairing stays)  
6. `chat_page.js` Invite Discord / starred-for-DMs copy  
7. Control panel / landing “Start Discord bot” / gateway status (those pages still talk like a process manager)  
8. `DISCORD_STATUS_PATH` writer  

Keep: token, `discord_cli`, `discord.post`, settings field “Discord Bot Token” (token is for REST).

**Risk:** users who still DM the bot for `/cursor` lose that surface; web chat is the replacement. Confirm before the delete PR.

**Status LED:** `/api/status` `discord_connected` would go stale unless replaced (e.g. optional REST `GET /users/@me`).

---

## `src/api/*.py` execution review (60 top-level modules)

Inbound refs: regex over tracked `.py/.js/.html/.md/.yaml` (see coverage ledger). This is **consumer classification**, not a line-read of every function.

| Module | Execution consumers (non-doc) | Keep / retire |
|---|---|---|
| `web_chat_api.py` | Daemon Flask child; almost all HTTP | Keep; strip graph/Telegram/Slack |
| `auth_db.py` | Chat, actions, workers, brain | Keep |
| `chat_run_registry.py` | kernel/jobs/stop | Keep |
| `chat_delivery.py` | chat HTTP, supervised | Keep |
| `cuttle_ui_capabilities.py` | compiler, actions | Keep |
| `cursor_agent_commands.py` | slash, cost, registry | Keep |
| `active_executions.py` | kernel, flask_restart, executing-jobs | Keep |
| `starred_slash.py` | `/api/chat` prefix, JS | Keep |
| `flask_restart.py` | daemon, actions | Keep |
| `job_watch.py` | project commands, workers CLI | Keep |
| `query_tracker.py` | **kernel** + graph finish HTTP | Keep module; drop node-editor-only APIs later |
| `project_actions.py` | action forms, **discord.post** | Keep |
| `auth_api.py` | sessions/OAuth | Keep |
| `action_forms.py` | chat rewrite | Keep |
| `gitea_client.py` | cuttle-jobs, actions | Keep |
| `agent_context.py` | CLI context HTTP | Keep |
| `model_pricing.py` | cost/context | Keep |
| `agent_usage.py` | harness adapters | Keep |
| `chat_widgets.py` | web_chat, widgets CLI | Keep |
| `llm_complete.py` | titler, enhancer, router brain | Keep |
| `process_kill_safety.py` | daemon, registry | Keep |
| `query_events.py` | kernel, cursor CLI | Keep |
| `auth_session.py` | authz, widgets | Keep |
| `chat_titler.py` | web + discord_chat_bridge | Keep (web); bridge goes with DM |
| `cursor_question_bridge.py` | cursor CLI | Keep |
| `session_keys.py` | delivery/registry/restart | Keep |
| `commit_message_suggester.py` | cuttle-jobs workspace | Keep |
| `http_authz.py` | workers, web_chat | Keep |
| `inference_mode.py` | web_chat, tests | Keep |
| `lan_access.py` | daemon, terminal | Keep |
| `project_commands.py` | web_chat | Keep |
| `shared_media.py` | daemon, web_chat | Keep |
| `vision_prepass.py` | `/api/chat` + Discord trigger | Keep |
| `chat_tts.py` | web_chat | Keep |
| `chat_warnings.py` | kernel | Keep |
| `cursor_plan_bridge.py` | cursor CLI | Keep |
| `cuttle_managed_process_guard.py` | registry, hook | Keep |
| `desktop_electron.py` | web_chat | Keep |
| `discovery_mdns.py` | web_chat | Keep |
| `limiter.py` | auth_api, web_chat | Keep |
| `mobile_*` | web_chat | Keep |
| `web_terminal.py` | web_chat | Keep |
| `agent_cost.py` | kernel | Keep |
| `chat_turn_idempotency.py` | web_chat | Keep |
| `discord_chat_bridge.py` | Discord trigger only | Retire with gateway chat |
| `fs_reveal.py` | web_chat | Keep |
| `internal_http.py` | import for `PIPELINE_AVAILABLE` only | Fold flag; delete helpers |
| `markdown_skills.py` | jev | Keep |
| `prompt_enhancer.py` | web_chat | Keep |
| `restart_safety_policy.py` | tests/docs; policy for kills | Keep |
| `starred_project.py` | web_chat | Keep |
| `task_benchmarks.py` | agent_cost | Keep |
| `video_playlists.py` | web_chat | Keep |
| `bundled_llm_tools.py` | web_chat | Keep |
| `chat_status_phases.py` | web_chat | Keep |
| `doctor.py` | web_chat | Keep |
| `pairing_manager.py` | web_chat (webchat + discord inbound) | Keep; drop telegram/slack channels |
| `sandbox_policy.py` | **tests only** | Optional with graphs; **not** the settings HTTP API |
| `__init__.py` | package | Keep |

**Still not execution-complete:** `src/api/agent_harness/**`, `agent_router/**`, `device_workers/**`, `dashboards/**`, `cuttle_jobs/**`, `src/web/js/chat_page.js` (partial), remaining 400+ `src/` py files.
