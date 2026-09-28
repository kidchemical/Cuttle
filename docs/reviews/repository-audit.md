# Repository-wide technical-debt audit

**Date:** 2026-09-27 · **HEAD:** `9ecd388` · **Application code:** not modified.

**Baselines** (must match [`repository-inventory.md`](repository-inventory.md)):

| Metric | Count |
|---|---|
| Tracked files | **914** |
| Untracked (not ignored) | **0** |
| Ignored file paths | **72628** (mostly `.venv` / `node_modules` / Gradle / `temp`) |
| `git status --short --untracked-files=all` | **0 lines** |

**Cleanliness vs reproducibility:** ignored files on this machine are largely legitimate runtime/cache. A **fresh clone** can boot using README venv + `src/.env.example` **if** operators do not follow the README line that starts Flask via `start_api_server.py` (that script is the port-5000 time-series app). `settings.json` is gitignored but **created with defaults** — not a silent hard dependency on this host’s 13k file.

**Coverage (honest):**

| Scope | Status |
|---|---|
| Every tracked path listed | **Yes** — inventory appendix |
| Ignored trees | **Directory classification**, not 72k file reads |
| Every `.py` / `.js` line | **No** — 505 Python + 35 JS files; reviewed by tree + execution traces + targeted grep |
| Dynamic HTTP/JS/subprocess | **Sampled** (chat path, workers blueprint, graph HTTP, Jobs UI fetch) |
| Vendor `claw-code` / `mcp-govee` internals | **Not** line-reviewed |

Incomplete work is called out as **uncertain** rather than confirmed dead.

---

## Findings (by subsystem)

Confidence: **H** high (trace + missing callers) · **M** medium · **L** low / needs runtime.

### A. Graph / node-editor era (legacy)

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| A1 | Graph JSON pipelines no longer ship | `git ls-files src/pipelines` empty; tests skip “pipeline graphs removed” | H | Keep 410/empty list until Jobs UI stops calling graph APIs |
| A2 | `POST /api/pipeline-run-now` still used by Jobs UI | `src/web/jobs_page.html` ~1558; `job_insight.html` ~312; handler in `web_chat_api.py` ~10397 — mix of 410 vs leftover `running_pipelines` mutation nearby | H | Either wire Jobs to workers/cuttle-jobs **or** stop fetching; then delete dict + handlers |
| A3 | `/api/execute-tool` and `/api/execute-output` have **no JS callers** | Grep `src/web` only DOM `nodeType`; Python: `execute_tool` → `_execute_remote_agent_tool` only | H (HTTP unused) | **Suspected** dead HTTP; `_execute_remote_agent_tool` still **calls** `_run_harness_web_command` — do not delete helper until chat/tests confirmed unused |
| A4 | `running_pipelines` in-memory dict still updated | `web_chat_api.py` ~350, ~10452 `pipeline-reload` still writes it | M | Remnant of graphs; `/api/status` still exposes `running_pipeline_count` |
| A5 | `pipeline-execution-finish`, `pipeline-job-status`, `pipeline-reload` not reduced to 410 | Security review 2026-09 already noted leftover bodies | H | Tombstone or delete after A2 |
| A6 | `/pipeline_chat.html` redirects to Jobs | Route ~2166; **file absent** (intentional redirect) | H | Fine as compatibility URL |
| A7 | `/node_editor.html` redirects to Router | Route ~2124; **file absent** | H | Fine; tray “Open Router” still named `on_node_editor` in `cuttle_daemon.py` ~1272 |
| A8 | Integration tests still talk about Discord_Remote_Code.json | `src/tests/integration/test_discord_remote_execution.py` skips if missing | H | Update tests when graphs stay gone |
| A9 | `query_tracker.py` still has `node_editor` context | ~220 | M | Rename/dead branch after UI gone |
| A10 | Settings defaults still mention `OOBE_Welcome` pipeline / `show_node_ids` | `settings_manager.py` FACTORY_DEFAULT_PIPELINE | M | Config debt, not runtime crash (defaults empty `default_pipeline`) |

**Replacement:** Agent harness + Jobs/workers/cuttle-jobs. **Cannot remove entire graph HTTP** until Jobs + `running_pipelines` consumers are traced in the browser.

### B. Launchers and docs drift (reproducibility)

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| B1 | README “Flask alone” → `start_api_server.py` | README ~113; script docstring + body starts **:5000** `time_series_api.py` | H | **Architectural/docs bug.** Flask-alone is `src/api/web_chat_api.py` or `src/scripts/launchers/start_web_chat.py` |
| B2 | `src/launcher.py` duplicates daemon | ~1100 lines start bot+API; daemon is canonical | M | Compatibility; confirm no Windows shortcut still uses it (`create_desktop_shortcuts.ps1`) |
| B3 | `start_web_chat.py` `chdir` is `Path(__file__).parent` = `launchers/` | ~18 | M | May be wrong cwd vs `src/` — **uncertain** without running |
| B4 | `AGENTS.md` names `_execute_remote_agent_tool` as core dispatch | AGENTS.md ~161; chat uses `_run_harness_web_command` / kernel | H | Doc fix; not a clone-breaker |
| B5 | e2e comment repeats start_api_server | `test_app_shell_navigation.py` ~12 | M | Comment-only |

### C. Duplicate / overlapping config

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| C1 | Five `bot_config.json` copies | root, `src/`, `electron/`, two test fixtures; sizes 502/518/358/159 | H | Trace `BotConfig` cwd (`src/core/config.py` `Path("bot_config.json")`) |
| C2 | `*.json` gitignore + allowlist | Easy to omit new source JSON | M | Document allowlist; `settings.json` correctly local |
| C3 | Hub `.cuttle/docs` vs `docs/guides` overlap | e.g. workers, discord | M | Intentional (agent runbooks vs public docs) — not dead |

### D. Frontend leftovers

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| D1 | `landing_page_backup.html` tracked | 1538 lines; no route found serving it | H | **Suspected** unused backup |
| D2 | `control_panel.html` still in nav | `shared_navigation.js`, `app_shell.js`, landing menus | H | **Live** old surface — product decision to keep or fold into Settings |
| D3 | Jobs page still graph-oriented copy | “pipelines/scheduled jobs” in `serve_jobs_page` docstring | M | UX debt |

### E. Auth / security residuals (not milestone reopen)

Tracked as GitHub **#1–#5**. Loopback worker runtime, LAN enroll, settings GET, TLS pin, HMAC replay **per action**.

### F. Oversized / coupling

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| F1 | `web_chat_api.py` ~15k lines | Map already exists; GitHub **#6** | H | Extract when blocked; not next product |
| F2 | Reverse imports into composition root | `dispatch.py`, `chat_delivery.py`, `auth_api.py`, `kernel.py` | H | Listed in WEB_CHAT_API.md |
| F3 | `chat_page.js` ~26k lines | Same class of bottleneck as Flask file | H | Separate from Python extract |

### G. Tests referencing removed behavior

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| G1 | Graph-skipping tests | `test_discord_remote_execution.py` | H | Keep skip or delete with graphs |
| G2 | `src/bot.py` shim only for unit tests | `test_security.py`, `test_username_case.py` import `bot` | H | Keep until tests migrate to `discord_bot` |

### H. Tools / MCP

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| H1 | `src/tools/` still ComfyUI, Govee, OCR, web_search | Matches AGENTS.md | H | **Live** |
| H2 | `src/core/mcp_tool_coaching.py` | Coaching for MCP; Cuttle does not host MCP server | M | May still inject into prompts — **not traced to runtime this pass** |
| H3 | `vendor/mcp-govee` submodule | `.gitmodules` | H | Optional; `git submodule update --init` |
| H4 | `vendor/claw-code` gitlink + gitignore of contents | README: opt-in clone | M | Clean clone has empty/unpopulated tree unless submodule protocol documented |

### I. Apps

| ID | Finding | Evidence | Confidence | Action |
|---|---|---|---|---|
| I1 | Three Android trees | `apps/mobile` (94), `android_companion` (16), `android_bt_voice` (37) | M | Confirm which APKs are shipping vs experiments |
| I2 | Mobile Gradle build dirs ignored | Regenerable | H | Fine |

### J. Electron

Tracked 16 files; `node_modules` ignored. Sandbox helper required on Ubuntu. TLS pin is **#2**, not sandbox.

### K. Local-only / scratch (Phase 1B)

| Path | Class | Reproducibility |
|---|---|---|
| `temp/WAG-EMS-original-backup.git` | 5 | Not needed for Cuttle |
| `temp/promo`, `readme_raw`, png dumps | 4+5 | README media rebuild scripts |
| `_personal/` Instacart credential **filenames** | 6 | Must never be committed |
| This host `src/settings.json` | 2 | Clone uses defaults |

### L. Independently found (not from user’s earlier examples)

1. README → `start_api_server.py` is the **wrong process** for Flask-alone (**B1**).
2. `/api/execute-tool` HTTP has **zero** frontend callers (**A3**).
3. Jobs UI still drives **removed** graph run API (**A2**).
4. Five `bot_config.json` files + cwd-relative load (**C1**).
5. `landing_page_backup.html` with no route (**D1**).
6. `AGENTS.md` core-dispatch sentence is **stale** (**B4**).
7. Daemon tray callback still named node editor (**A7**).
8. `*.json` blanket ignore vs allowlist — risk of missing templates (**C2**).

---

## Intentional compatibility (do not treat as junk)

- `/node_editor.html` and `/pipeline_chat.html` **redirects**
- Process start/stop **410** JSON
- Many `/api/save-pipeline` etc. **410**
- `src/bot.py` test shim
- Unsigned historical action cards **fail closed** (hardening)

---

## Shared dependencies to preserve if graphs die

- `_run_harness_web_command` / `agent_harness` — **chat**
- `query_tracker` — query log inspector (product)
- Discord bot — **not** graph-only
- `running_pipelines` — only if something still displays it (`/api/status`, Jobs)

---

## Coverage gaps (next session)

- Line-level review of `src/scripts/utilities/*.py` (33 files)
- Whether `control_panel.html` is reachable from **app_shell** primary nav vs leftover landing
- Whether `execute_tool` is hit by Discord or internal HTTP (`internal_http.py`)
- `src/launcher.py` consumers (shortcuts, docs)
- Android app which is canonical
- Full `src/web/js/chat_page.js` dead functions — not done
