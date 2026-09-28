# Repository-wide technical-debt audit

**Date:** 2026-09-27 (pass 3) · **HEAD:** `4f2880c` · **Application code:** not modified.

Pass 3 product decisions and consumer tables: [`graph-discord-consumers.md`](graph-discord-consumers.md). Coverage: [`coverage-ledger.md`](coverage-ledger.md).

CH-000743-21 AST/ledger is **not** treated as a finished execution review. This pass classified **all 60** `src/api/*.py` modules by inbound consumers and traced graph/Jobs/Discord/Telegram paths. Remaining trees (harness packages, `chat_page.js`, scripts) are still incomplete.

**Baselines**

| Metric | `9ecd388` (first pass) | `4f2880c` (this pass) |
|---|---|---|
| Tracked files | 914 | **918** (four audit docs committed) |
| Inventory data rows | 914 path bullets | **918** table rows; `set(git ls-files)` matches |
| Untracked (not ignored) | 0 | 0 |
| Ignored paths | 72628 (dir-classed, not re-walked) | same assumption |
| `git status --short --untracked-files=all` | 0 | dirty only if this continuation’s docs are uncommitted |

**Cleanliness vs reproducibility**

Ignored trees on this machine (venv, DBs, certs, `settings.json`, `_personal/`, `temp/`) are **legitimate local state**, not repository dirt. A clone does **not** need them.

A clone **can** fail to match *documented* Flask-alone: README names `src/scripts/start_api_server.py`, which starts **:5000** `time_series_api.py`. Canonical Flask-alone (what the daemon runs) is `python src/api/web_chat_api.py` from repo root.

`src/scripts/launchers/start_web_chat.py` is **not** a substitute: it `chdir`s to `src/scripts/launchers/` and `subprocess.run([python, "web_chat_api.py"])`. That basename is `src/api/web_chat_api.py`, not under `launchers/`. **Confirmed broken** by reading the launcher; not executed (would fail `FileNotFoundError`).

**Clean-install run:** still **not** performed in an empty clone. Daemon boot is inferred from `cuttle_daemon.py` + `start_cuttle.sh`, not observed.

---

## Coverage (this pass vs original acceptance)

| Criterion | Status |
|---|---|
| Every tracked path listed with class, owner, purpose, review | **Yes** — inventory table, 918 rows |
| Inventoried ≠ reviewed | **Yes** — review column: `inventoried` / `structure` / `execution` / `partial-execution` |
| Every maintained `.py` inspected | **AST parse of all 505** (0 syntax failures) = `structure`. **Not** every function body. Oversized modules are `partial-execution`. |
| Every maintained `.js` inspected | **Header/prefix read of all 35**; `chat_page.js` / `app_shell.js` partial-execution |
| Dynamic HTTP/JS/subprocess | Graph + Jobs + Discord planes + Telegram/Slack: **traced** (see graph-discord-consumers). Not every `web_chat_api` handler. |
| `src/api/*.py` (60 files) | **Consumer-classified** this pass; not every function body |
| Every maintained `.py` inspected | AST all 505; **execution** only where ledger/consumers say so |
| Android Java/Gradle, Electron `node_modules` | Inventoried; not line-reviewed |
| Vendor `claw-code` / `mcp-govee` internals | Not line-reviewed |
| Fresh clone install | **Not run** |

Histogram (HEAD `4f2880c`): inventoried 375 · structure 525 · execution 13 · partial-execution 5 (plus a few review promotions in the inventory after the generator).

---

## Findings (by subsystem)

Confidence: **H** high · **M** medium · **L** low.

### A. Graph / node-editor era

| ID | Finding | Evidence | Confidence | Dead vs live |
|---|---|---|---|---|
| A1 | Graph JSON pipelines no longer ship | `git ls-files src/pipelines` empty | H | Confirmed gone as product |
| A2 | Jobs UI still contains graph `fetch` helpers | `startJob`/`runNow` in `jobs_page.html`; `loadPipelines` already forces `allJobs=[]`. Live Jobs data is `cuttle-jobs` + workers | H | **Retired graphs:** delete graph browse/JS; **keep** cuttle-jobs/workers |
| A3 | `/api/execute-tool` has no JS callers | Grep `src/web`: no `/api/execute-tool`. Handler ~11739 still implements node types | H HTTP unused from UI | **Suspected** dead HTTP. Discord `execute_tool_command` is a **retired stub** (returns string; does not POST this route). `internal_http._internal_app_post` is **never called**. Tests: `test_discord_remote_execution.py` string-asserts `_execute_remote_agent_tool` exists. **Do not delete** `_execute_remote_agent_tool` until chat/local-LLM confirmed unused — it is still defined and used **from** `execute_tool()`. |
| A4 | `running_pipelines` **writes** | Module dict `{}` at ~350. Only assignment to keys is in `pipeline_reload()` **after** `return jsonify(...)` at ~10454. `pipeline-register-running` is 410. **No reachable writer.** | H | **Confirmed:** dict stays empty. **Readers still live:** `/api/status` `running_pipeline_count`; `/api/running-pipelines`; `/api/jobs` **dead tail** after empty return (~2308); `/api/job-insight` ~3194; `/api/sessions/list` ~9883; `/api/pipeline-check-running`; `/api/pipeline-job-status`. They always observe empty/stopped. |
| A5 | Tombstone remnants (unreachable bodies) | `pipeline_reload` ~10454–10478 unreachable. `api_jobs` ~2308 returns empty then ~88 lines of graph listing (~2309–2396) unreachable. | H | **Confirmed dead statements** inside live functions. Compatibility **response** is the early return. |
| A6 | `/pipeline_chat.html` redirect | Route exists; file absent | H | Intentional compatibility |
| A7 | `/node_editor.html` redirect; tray `on_node_editor` | `cuttle_daemon.py` | H | Intentional name debt |
| A8 | Integration tests mention missing graph JSON | `test_discord_remote_execution.py` skips | H | Stale test surface |
| A9 | `query_tracker` `node_editor` context | ~220 | M | Suspected leftover branch |
| A10 | Settings `OOBE_Welcome` / `show_node_ids` | `settings_manager.py` | M | Config debt |
| A11 | `/api/job-insight` still loads graph JSON from settings path | ~3164–3178; 404 if file missing | H | **Live handler**, empty product data |
| A12 | Telegram/Slack HTTP | `_handle_external_trigger` only those two routes; **no** `src/web` callers | H | **Remove** (unwanted). Executable ≠ wanted |
| A13 | `active_executions` | `register_execution` from `agent_harness/kernel.py` | H | **Live** — **preserve** when graph HTTP dies |
| A14 | `pipeline-execution-finish` | Live body ~10257; caller `create_test_execution.py` only | H | Delete with graphs; kernel uses `query_tracker` in-process |
| A15 | `force_reload.js` | No HTML include; expects `PipelineExecutor` | H | Abandoned |
| A16 | `sandbox_policy.py` vs `/api/settings/sandbox` | Policy module: no production import. **HTTP:** POST owner-only (`require_owner`); tests `test_http_authz.py`. GET unauthenticated ([#4](https://github.com/kidchemical/Cuttle/issues/4), hardening doc). No `src/web` fetch. `get_sandbox_config` not read by harness. | H | **Do not** put the settings API in graph removal. Policy `.py` is a separate optional delete. Cursor `/sandbox` slash ≠ this API. |

**Replacement:** harness + cuttle-jobs + workers. Graph HTTP removable after Jobs JS cleanup. Discord **gateway** chat is a separate stream. Kernel `active_executions` stays.

### B. Launchers and docs (reproducibility)

| ID | Finding | Evidence | Confidence | Dead vs live |
|---|---|---|---|---|
| B1 | README Flask-alone → `start_api_server.py` | README ~113; script starts `scripts/time_series_api.py` :5000 | H | **Docs bug.** Correct command: `python src/api/web_chat_api.py`. **Do not** recommend `start_web_chat.py`. |
| B2 | `src/launcher.py` vs daemon | Starts `api/web_chat_api.py` with cwd `src/` (~517) | M | Overlapping supervisor; no daemon restart protocol |
| B3 | `start_web_chat.py` broken | `Path(__file__).parent` = `launchers/`; runs `"web_chat_api.py"` | H | **Confirmed broken launcher** (not uncertain) |
| B3b | `start_router_editor.py` broken | `from web_chat_api import app` after inserting `launchers/` on `sys.path` | H | Confirmed broken |
| B3c | `start_ungit.py` broken | `from project_manager import project_manager`; real module `src/managers/project_manager.py` | H | Confirmed broken |
| B3d | `launcher_debug.py` broken | Looks for `launchers/web_chat_api.py`; class still named `JamBitDebugLauncher` | H | Confirmed broken |
| B4 | `AGENTS.md` core dispatch | Names `_execute_remote_agent_tool`; chat uses `_run_harness_web_command` / kernel | H | Doc only |
| B5 | e2e comment `start_api_server.py` | `test_app_shell_navigation.py` ~12 | M | Comment-only |
| B6 | Electron README `python ../src/kill_bots.py` | File is `src/scripts/utilities/kill_bots.py`; that script `from launcher import JamBitLauncher` with `sys.path` = `utilities/` | H | Stale doc + **broken script** |
| B7 | `debug_cursor_location.py` | `from tool_manager import find_cursor_exe`; **no** `tool_manager.py` in repo | H | Confirmed broken |
| B8 | `hello_world.py` | Prints Hello World; utilities README lists it as test script | H | Abandoned sample |
| B9 | No product UI caller of `/api/timeseries` | No matches in `src/web` or `src/api/dashboards`. `run_tests_with_logging.py` + `tests/html_reporter.py` use `test_history_manager.get_time_series_data` **in-process**, not port 5000. | H unused **HTTP :5000**; M whether anyone still starts that process by habit |

### C. Duplicate / overlapping config

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| C1 | Five `bot_config.json` copies | root, `src/`, `electron/`, test fixtures; `BotConfig` uses `Path("bot_config.json")` cwd-relative | H | Trace cwd at Discord vs Flask start |
| C2 | `*.json` gitignore + allowlist | Easy to omit new source JSON | M | Document; not a current untracked miss |
| C3 | Hub `.cuttle/docs` vs `docs/guides` | Intentional split | H | Keep |

### D. Frontend leftovers

| ID | Finding | Evidence | Confidence |
|---|---|---|---|
| D1 | `landing_page_backup.html` | No route | H **suspected** unused backup |
| D2 | `control_panel.html` in landing + `app_shell` title map + `shared_navigation.js` | Live links | H **live** old surface |
| D3 | Jobs copy still graph-oriented | serve_jobs docstring / UI | M UX |

### E. Auth / security

GitHub **#1–#5**. Not reopened here.

### F. Oversized / coupling

| ID | Finding | Evidence | Confidence |
|---|---|---|---|
| F1 | `web_chat_api.py` ~15k lines | WEB_CHAT_API.md | H |
| F2 | Reverse imports into composition root | dispatch, chat_delivery, kernel | H |
| F3 | `chat_page.js` ~26k | Partial review only | H |
| F4 | `internal_http` helpers unused | Import in `web_chat_api` ~168 sets `PIPELINE_AVAILABLE`; `_internal_app_post` never referenced again in repo | H | **Live import / dead functions.** Deleting the module without moving the flag would set `PIPELINE_AVAILABLE = False` and 503 chat. |

### G. Tests

| ID | Finding | Evidence | Confidence |
|---|---|---|---|
| G1 | Graph-skipping tests | `test_discord_remote_execution.py` | H |
| G2 | `src/bot.py` test shim | `test_security.py` etc. | H keep |

### H. Tools / MCP

| ID | Finding | Evidence | Confidence |
|---|---|---|---|
| H1 | `src/tools/` ComfyUI, Govee, OCR, web_search | AGENTS.md | H live |
| H2 | `mcp_tool_coaching.py` | Called from `web_chat_api.py` ~10720 | H **live** prompt suffix (name is leftover “MCP”) |
| H3 | `vendor/mcp-govee` | `.gitmodules` | H optional |
| H4 | `vendor/claw-code` gitignore contents | README opt-in | M |

### I. Apps / J. Electron

Unchanged: three Android trees inventoried, not APK-provenance traced. Electron 16 tracked files.

### K. Local-only (Phase 1B)

Unchanged directory classes. No secrets quoted.

### L. Independently found this continuation

1. **A4 correction:** `pipeline-reload` does **not** update `running_pipelines` at runtime (unreachable). Prior audit was wrong.
2. **`api_jobs` same tombstone pattern** as reload (empty return, then dead graph listing).
3. **Four `src/scripts/launchers/` files all broken** (cwd/`sys.path`), not just `start_web_chat.py` “uncertain”.
4. **`internal_http` load-bearing unused import.**
5. **`tool_manager` missing** — `debug_cursor_location.py` cannot import.
6. **`kill_bots` / Electron README path drift.**
7. Telegram/Slack are **unwanted** — remove routes and settings; do not keep because they share `process_message_with_bot`.
8. **`active_executions` is harness-live** — not a graph-only helper.

---

## Intentional compatibility (not junk)

- HTML redirects for removed pages
- Process-control **410**
- Many `/api/*pipeline*` **410** with `_graph_pipelines_gone_response`
- `src/bot.py` test shim
- Unsigned historical action cards fail closed

---

## Shared dependencies to preserve if graph HTTP is slimmed

- `_run_harness_web_command` / `agent_harness` — chat
- `active_executions` — kernel + `/api/executing-jobs` (`app_shell.js` ~6081)
- `process_message_with_bot` / `/api/chat` — web agents
- `query_tracker` — kernel (strip node-editor branches later)
- `PIPELINE_AVAILABLE` flag semantics

---

## Incomplete investigations (explicit)

- Not every `@app.route` body in `web_chat_api.py`
- Not every function in `chat_page.js`
- Control Panel contents vs Settings (D2 live links; page not fully walked)
- `src/api/dashboards` has **no** timeseries/5000 refs; B9 still not a process-list on this host
- Which `bot_config.json` wins for daemon vs `launcher.py` vs tests
- Which Android app is shipping
- `GET /api/pipeline-chats` UI callers
- `src/api/agent_harness/**`, `agent_router/**`, `device_workers/**` package execution (not this pass’s 60-file table)
- Full `chat_page.js` (Invite Discord + starred-for-DMs must stay until gateway retirement is approved)
- Fresh-clone install
- Escape Purgatory `discord-post.yaml` not opened (EP not this workspace)
