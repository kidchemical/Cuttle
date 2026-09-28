# Cleanup plan (dependency-aware, small workstreams)

**Date:** 2026-09-27 · **HEAD at audit:** `9ecd388`  
**Rule:** do not mix these with feature work; each stream is independently revertable.  
**This audit did not change application code.**

Product work (workers, GUI, dashboards) stays **ahead of** large Flask extraction ([issue #6](https://github.com/kidchemical/Cuttle/issues/6)).

---

## Workstream 0 — Documentation truth (no behavior change)

**Goal:** Clone/README match actual entry points.

| Step | Change | Verify |
|---|---|---|
| 0.1 | README Flask-alone: `src/api/web_chat_api.py` (or documented launcher that actually binds **8080**) | Fresh-clone dry-run of the listed command; confirm it is not `:5000` |
| 0.2 | AGENTS.md: core dispatch is `_run_harness_web_command` / `agent_harness.kernel` | Grep AGENTS for `_execute_remote_agent_tool` |
| 0.3 | e2e comment in `test_app_shell_navigation.py` | Comment-only |
| 0.4 | Tray label `on_node_editor` → “Open Router” naming in `cuttle_daemon.py` | Tray still opens `/router_editor.html` |

**Tests:** none required beyond link check. **Risk:** low.

---

## Workstream 1 — Jobs UI vs graph APIs

**Depends on:** product decision: is Jobs still a graph runner or a workers/cuttle-jobs cockpit?

| Step | Change | Verify |
|---|---|---|
| 1.1 | Trace every `fetch('/api/pipeline-*')` in `jobs_page.html` / `job_insight.html` | Browser or grep list |
| 1.2 | Point live buttons at workers / cuttle-jobs **or** hide buttons | `test_cuttle_jobs.py` / worker tests; manual Jobs page |
| 1.3 | Then 410 or delete leftover `running_pipelines` writers (`pipeline-reload`, job-status, execution-finish) | `GET /api/status` still 200; no Jobs 500s |
| 1.4 | Slim `query_tracker` `node_editor` branch | Query log inspector still opens |

**Do not** delete `_run_harness_web_command`.

**Regression:** `src/tests/test_http_authz.py` 410 process-control; add a test that `pipeline-run-now` stays 410 **or** the new Jobs API.

---

## Workstream 2 — Graph HTTP with no frontend (`/api/execute-tool`)

**Depends on:** grep + optional logging in production for 1 week (or access logs).

| Step | Change | Verify |
|---|---|---|
| 2.1 | Confirm no `internal_http` / Discord calls `execute_tool` | Grep + optional 404 counter |
| 2.2 | Return 410 from `execute_tool` / `execute-output` like other graph routes **or** delete after 2.1 | Chat `/cursor` still works (`test_agent_harness.py`, live turn) |
| 2.3 | Keep `_execute_remote_agent_tool` until 2.2 proven; then fold leftover into harness-only | `test_remote_agent.py` updated |

**Risk:** medium if something still POSTs tool nodes.

---

## Workstream 3 — Duplicate config and backups

| Step | Change | Verify |
|---|---|---|
| 3.1 | Single `bot_config.json` owner; tests use fixtures only | `BotConfig` loads; Discord still starts |
| 3.2 | Delete or stop tracking `src/web/landing_page_backup.html` | No route; grep |
| 3.3 | Document `*.json` allowlist in README or `.gitignore` comment | New JSON templates listed in `!` negations |

---

## Workstream 4 — Launchers

| Step | Change | Verify |
|---|---|---|
| 4.1 | Inventory who runs `src/launcher.py` / `create_desktop_shortcuts.ps1` | Windows/Linux |
| 4.2 | Deprecate launcher in favor of `cuttle_daemon.py` | Daemon restart still works |
| 4.3 | Rename or wrap `start_api_server.py` so it cannot be confused with 8080 | README 0.1 |

**Tests:** `test_flask_restart.py`, `test_cuttle_managed_process_guard.py`.

---

## Workstream 5 — Control panel / landing nav

Product UX: fold Control Panel into Settings/About or keep.

| Verify | `app_shell.js` nav, mobile, Electron |
|---|---|

---

## Workstream 6 — Security backlog (separate issues)

Do **not** reopen hardening. Implement independently:

- [#1](https://github.com/kidchemical/Cuttle/issues/1) workers identity  
- [#2](https://github.com/kidchemical/Cuttle/issues/2) Electron TLS  
- [#3](https://github.com/kidchemical/Cuttle/issues/3) adapter checksums  
- [#4](https://github.com/kidchemical/Cuttle/issues/4) settings GET  
- [#5](https://github.com/kidchemical/Cuttle/issues/5) HMAC replay **per action class**

**Tests:** `test_http_authz.py`, `test_device_workers.py`, `test_action_forms.py`.

---

## Workstream 7 — Flask modularization (last)

Only if F1/F2 slow features. Start from [`docs/guides/WEB_CHAT_API.md`](../guides/WEB_CHAT_API.md). Issue [#6](https://github.com/kidchemical/Cuttle/issues/6).

**Fence:**

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

Plus a live `/cursor` turn after Flask restart.

---

## Workstream 8 — Local scratch (optional, operator)

`temp/` (including `WAG-EMS-original-backup.git`) is **not** required for Cuttle. Do not delete from an agent session without the owner confirming. `_personal/` stays gitignored.

---

## Recommended `.gitignore` corrections (do not apply in this audit)

| Idea | Why |
|---|---|
| Comment block listing every `!*.json` exception | Stops accidental untracked source JSON |
| Optionally ignore `src/web/landing_page_backup.html` if deleted from git | After 3.2 |
| Confirm `/vendor/claw-code/` ignore vs submodule UX | Clone docs |

**Do not** ignore `src/settings.json` tracking “to make clones identical” — it holds install preferences; defaults-on-missing is the reproducibility story.

---

## Order

`0 → 1 → 2` (graph/docs) can proceed without #6.  
`4` after confirming shortcuts.  
`6` anytime as security tickets.  
`7` when the monolith blocks product work.

---

## What this plan will not do

- Delete `_execute_remote_agent_tool` in the same PR as Jobs UI.
- `git submodule` force-add `claw-code`.
- Wipe `temp/` or `_personal/` from the agent.
- Extract 15k lines of `web_chat_api.py` as a single change.
