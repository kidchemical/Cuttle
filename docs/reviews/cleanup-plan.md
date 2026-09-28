# Cleanup plan (gated on investigation)

**HEAD at this continuation:** `4f2880c` · **Application code not changed.**

This document is **not** a license to start deleting graph HTTP or launchers. The previous version jumped to implementation. **Workstream 0 is coverage and corrections.** Implementation streams start only when the inventory review column and the audit’s “incomplete investigations” list say that subsystem is closed.

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

**Remaining structure-only closures (priority order for a later session):**

1. `src/api/*.py` except `web_chat_api.py` (partial) — especially unused vs kernel.
2. `src/scripts/utilities/*_cli_tool.py` and session stores (harness live; samples vs dead).
3. `src/web/js/*.js` besides `app_shell.js` / `chat_page.js`.
4. `src/api/dashboards/` vs port-5000 time-series (**B9**).
5. Android trees — which APK is canonical.

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

## Workstream 1 — Jobs UI vs graph APIs

**Depends on:** product decision (Jobs = workers/cuttle-jobs vs leftover graph cockpit). **Depends on:** A4/A5 understood (empty `running_pipelines`; unreachable writers).

| Step | Change | Verify |
|---|---|---|
| 1.1 | List every `fetch('/api/pipeline-*')` and `/api/job-insight` in Jobs HTML | Grep + browser |
| 1.2 | Point buttons at workers / cuttle-jobs **or** hide; expect 410 on `pipeline-run-now` until then | Manual Jobs page |
| 1.3 | After UI stops depending on empty dict: delete **unreachable** tails (`pipeline_reload` after return; `api_jobs` after empty return) and/or 410 `pipeline-job-status` / `pipeline-check-running` | `GET /api/status` 200; `running_pipeline_count` may stay 0 |
| 1.4 | **Do not** 410 `/api/pipeline-trigger-discord` or Telegram/Slack chat adapters | Discord DM still works |
| 1.5 | **Do not** delete `active_executions` | `/api/executing-jobs` + kernel |

**Regression:** `test_http_authz.py` process-control 410; add assertion `pipeline-run-now` is 410 **or** new Jobs API; live `/cursor` still registers/unregisters executions.

---

## Workstream 2 — `/api/execute-tool` HTTP

**Depends on:** 0.5 caller pass (Discord stub already traced; `internal_http` does not POST it).

| Step | Change | Verify |
|---|---|---|
| 2.1 | Optional access-log / 1-week 404 counter in production | Ops |
| 2.2 | 410 `execute_tool` / `execute-output` **or** delete routes after 2.1 | Chat `/cursor` still works |
| 2.3 | Keep `_execute_remote_agent_tool` until no remaining in-process caller | `test_remote_agent.py` |

**Risk:** medium if an external graph runner still POSTs.

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

`0` (ledger) is **done enough to resume**; `0.5` continues forever as structure→execution.  
`0b` can land as docs-only.  
`1 → 2` only after Jobs product decision and A4/A14 caller lists.  
`4` after shortcuts.  
`6` anytime.  
`7` when the monolith blocks product work.

---

## What this plan will not do

- Recommend `start_web_chat.py` as Flask-alone.
- Delete `_execute_remote_agent_tool` or `active_executions` with Jobs UI.
- 410 Discord/Telegram chat triggers because their URLs contain `pipeline`.
- `git submodule` force-add `claw-code`.
- Wipe `temp/` or `_personal/` from the agent.
- Extract 15k lines of `web_chat_api.py` as a single change.
- Declare graph HTTP fully dead because `running_pipelines` is never written.
