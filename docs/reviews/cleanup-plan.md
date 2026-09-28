# Cleanup plan (gated on investigation)

**HEAD:** `4f2880c` · **Application code not changed.**

Graphs and Telegram/Slack are **decided removals** (stream 1 / 1b) with consumer tables in [`graph-discord-consumers.md`](graph-discord-consumers.md). Gateway Discord chat is **not** deleted until stream 1c is approved. Workstream 0 remains the execution-review ledger.

Product work (workers, GUI, dashboards) stays **ahead of** Flask extraction ([issue #6](https://github.com/kidchemical/Cuttle/issues/6)).

---

## Workstream 0 — Investigation ledger (no behavior change)

**Goal:** Anyone can see what was reviewed, what remains, and the evidence for each conclusion. **Do not** treat this as a coding sprint.

| Step | Artifact | Done this pass? | Verify |
|---|---|---|---|
| 0.1 | File-level inventory: class, owner, purpose, review for **every** tracked path | Yes — [`repository-inventory.md`](repository-inventory.md) **918** rows = `git ls-files` | `python3` set-equality vs `git ls-files` |
| 0.2 | Coverage histogram + resume file | Yes — [`coverage-ledger.md`](coverage-ledger.md) | Counts match inventory review column (allowing later promotions) |
| 0.3 | Correct false findings before any removal | Yes — A4 `running_pipelines` writers unreachable; B3 launchers **broken**, not Flask-alone | Re-read `pipeline_reload` ~10454; `start_web_chat.py` ~17–65 |
| 0.4 | Record clean-install vs cleanliness | Yes in audit; **clone not actually run** | Optional: empty dir + README daemon steps |
| 0.5 | Promote `structure` → `execution` by subsystem | **Open** — 500+ py/js still structure-only | Pick one owner prefix per session; grep callers; update ledger |

**Remaining execution review (0.5):**

1. `src/api/agent_harness/`, `device_workers/`, `cuttle_jobs/`, `agent_router/` packages.
2. `chat_page.js` Discord invite (blocks 1c).
3. Android which-APK.
4. `GET /api/pipeline-chats` UI.

**Tests:** none. **Risk:** none if no app edits.

---

## Workstream 0b — Documentation truth (still no runtime change except docs)

**Depends on:** 0.3 (do **not** document `start_web_chat.py` as Flask-alone).

| Step | Change | Verify |
|---|---|---|
| 0b.1 | README Flask-alone: `python src/api/web_chat_api.py` (repo root / same as daemon). Mention `start_api_server.py` only as **:5000 time-series**. | Command is not `:5000`. **No** `start_web_chat.py`. |
| 0b.2 | AGENTS.md: core dispatch `_run_harness_web_command` / `agent_harness.kernel` | Grep `_execute_remote_agent_tool` |
| 0b.3 | e2e comment in `test_app_shell_navigation.py` | Comment-only |
| 0b.4 | Electron README `kill_bots.py` path | Points at a file that exists **or** drop the instruction |
| 0b.5 | Tray `on_node_editor` naming (optional) | Still opens `/router_editor.html` |

**Tests:** none required. **Risk:** low.

---

## Workstream 1 — Remove graph UI from Jobs; keep workers + cuttle-jobs

**Decision:** graphs and node editor are **retired**. Jobs’ live surfaces are `GET /api/cuttle-jobs` and `/api/workers/*`. `loadPipelines()` already returns empty.

| Step | Change | Verify |
|---|---|---|
| 1.1 | Remove graph browse/render/`startJob`/`runNow`/`job_insight` links from `jobs_page.html` | Manual Jobs: mesh jobs + Gitea history still load; no `pipeline-*` fetch |
| 1.2 | Remove or stub `job_insight.html` + `/api/job-insight` | No 500s from remaining nav |
| 1.3 | Delete unreachable graph tails and unused graph routes (`pipeline-reload` body, `api_jobs` tail, job-status, check-running, execution-finish, record-node, execute-tool/output, pipeline-settings, `force_reload.js`, `create_test_execution.py`). Optional: `sandbox_policy.py` **only if** still unimported. **Do not** remove `GET`/`POST /api/settings/sandbox` or `SettingsManager` sandbox accessors. | `GET /api/status` 200; **`/api/executing-jobs` still lists harness runs**; `test_http_authz.py` sandbox POST tests still pass |
| 1.4 | **Do not** delete `active_executions`, kernel, or `query_tracker` | live `/cursor` |
| 1.5 | `/api/pipeline-trigger-discord` | **Done in 1c** — route removed |

**Regression:** `test_http_authz.py`; `test_cuttle_jobs.py`; `test_device_workers.py`; `test_agent_harness.py`; browser Jobs page (cuttle-jobs + workers only).

---

## Workstream 1b — Remove Telegram and Slack

| Step | Change | Verify |
|---|---|---|
| 1b.1 | Delete telegram/slack routes and `_handle_external_trigger` if unused | Grep `telegram`/`slack` in `src/` |
| 1b.2 | Drop settings `channels.telegram/slack` and session kinds | Settings GET/POST channels only webchat (+ discord until 1c) |
| 1b.3 | Landing marketing copy | No Slack-as-product claims |

**Tests:** pairing/rate-limit tests that POST those routes.

---

## Workstream 1c — Discord gateway DM/guild AI chat — **complete (2026-09)**

**Kept:** `python -m api.discord_cli`, `discord.post`, optional `DISCORD_TOKEN` for REST only, `{project}/.cuttle/actions/discord-post.yaml`.

**Removed:** daemon `discord_bot.py`, `/api/pipeline-trigger-discord`, `discord_chat_bridge.py`, Invite-Discord UI, inbound Discord gateway. Token does not spawn a gateway.

Deliverable: [`discord-cleanup-2026-09.md`](discord-cleanup-2026-09.md). Architecture: [`../architecture/extension-boundaries.md`](../architecture/extension-boundaries.md).

| Step | Change | Verify |
|---|---|---|
| 1c.1 | Confirm no remaining DM `/cursor` users | Operator |
| 1c.2 | Stop spawning bot in `cuttle_daemon.py`; delete `discord_bot.py` | REST `discord_cli messages` and a `discord.post` confirm still work **without** the gateway process |
| 1c.3 | `/api/status` `discord_connected` | Do not leave a stale file LED; optional REST `/users/@me` or omit |
| 1c.4 | Classify `src/bot.py` tests; do not delete until tests migrate | `test_security.py` |
| 1c.5 | `bot_config.json` / `get_config()` | Still used by query_tracker / local_llm — **not** deleted with the gateway |

**Tests:** `test_discord_cli.py`, `test_project_actions.py`, `test_action_forms.py`; **do not** require `discord.py` gateway.

---

## Workstream 2 — `/api/execute-tool` (part of stream 1.3)

Only called from graph HTTP. `_execute_remote_agent_tool` has **no** chat callers (chat uses `_run_harness_web_command`). Delete with graphs; update `test_remote_agent.py` / integration string asserts.

---

## Workstream 3 — Duplicate config and backups

| Step | Change | Verify |
|---|---|---|
| 3.1 | Single `bot_config.json` owner after cwd trace | Discord + Flask start |
| 3.2 | Stop tracking `landing_page_backup.html` if still unrouted | Grep routes |
| 3.3 | Document `*.json` allowlist | New templates listed |

---

## Workstream 4 — Launchers

**Do not “fix” `start_web_chat.py` as the README Flask-alone.** Prefer documenting `web_chat_api.py` and leaving broken launchers for a dedicated deletion PR after grep of shortcuts.

| Step | Change | Verify |
|---|---|---|
| 4.1 | Inventory `src/launcher.py` / `create_desktop_shortcuts.ps1` consumers | Windows/Linux |
| 4.2 | Deprecate `src/scripts/launchers/*` (all four confirmed broken) | Daemon still boots |
| 4.3 | Rename or comment `start_api_server.py` so it cannot be confused with 8080 | README 0b.1 |
| 4.4 | `kill_bots.py` / `hello_world.py` / `debug_cursor_location.py` | Only after no docs/scripts reference |

**Tests:** `test_flask_restart.py`, `test_cuttle_managed_process_guard.py`. **Never** `taskkill` Flask from an agent.

---

## Workstream 5 — Control panel / landing nav

Product UX. Verify `app_shell.js`, mobile, Electron.

---

## Workstream 6 — Security backlog

[#1](https://github.com/kidchemical/Cuttle/issues/1)–[#5](https://github.com/kidchemical/Cuttle/issues/5) independently. HMAC replay **per action class**.

---

## Workstream 7 — Flask modularization (last)

[`docs/guides/WEB_CHAT_API.md`](../guides/WEB_CHAT_API.md). Issue [#6](https://github.com/kidchemical/Cuttle/issues/6).

Fence pytest list unchanged from prior plan (`test_http_authz`, action forms, agent stop, starred agent, flask restart, device workers) plus a live `/cursor` after restart.

If touching `PIPELINE_AVAILABLE`, **do not** drop `internal_http` import without replacing the flag (F4).

---

## Workstream 8 — Local scratch (operator)

`temp/`, `_personal/`: not required for Cuttle. Do not delete from an agent session without owner confirmation.

---

## Recommended `.gitignore` corrections (do not apply in this audit)

Same as before: comment the `*.json` allowlist; do not track `settings.json` “for clones.”

---

## Order

`0` / `0.5` continue execution review of harness packages.  
`0b` docs-only anytime.  
`1` graph Jobs strip (graphs **retired**).  
`1b` Telegram/Slack.  
`1c` gateway Discord **after** operator OK.  
`2` folded into `1.3`.  
`4` launchers. `6` security. `7` Flask extract last.

---

## What this plan will not do

- Recommend `start_web_chat.py` as Flask-alone.
- Delete `active_executions` or the agent kernel with graph HTTP.
- Delete `discord_cli` / `discord.post` / `DISCORD_TOKEN` with the gateway bot.
- Delete `src/bot.py` before its tests migrate.
- `git submodule` force-add `claw-code`.
- Wipe `temp/` or `_personal/` from the agent.
- Extract 15k lines of `web_chat_api.py` as a single change.
- Delete `GET`/`POST /api/settings/sandbox` in a graph-cleanup PR (security API + #4; no HTML fetch ≠ unused).
