# Coverage ledger (resume here)

**HEAD:** `4f2880c` · tracked `918`. Do not re-run a full `git ls-files` inventory unless the count changes.

Generator histogram (before a few manual review promotions): inventoried 375 · structure 525 · execution 13 · partial-execution 5.

Python: **505/505 AST-parsed** (0 failures). JS: **35/35** header-read.

## Parse failures

None.

## Execution / partial-execution (do not re-trace unless code moved)

- `electron/main.js`
- `src/api/web_chat_api.py` — **partial** (graph leftovers, chat dispatch, Jobs-related routes; not every handler)
- `src/api/internal_http.py` — imported only for `PIPELINE_AVAILABLE`; helpers unused
- `src/api/active_executions.py` — kernel + executing-jobs
- `src/bot.py` — test shim
- `src/bots/discord_bot.py` — DM → `/api/pipeline-trigger-discord`
- `src/core/config.py` — cwd-relative `bot_config.json`
- `src/core/mcp_tool_coaching.py` — called from web_chat_api ~10720
- `src/launcher.py`
- `src/managers/settings_manager.py`
- `src/scripts/cuttle_daemon.py` — **partial**
- `src/scripts/launchers/*` (4) — all broken cwd/imports
- `src/scripts/start_api_server.py` / `time_series_api.py` — :5000; no `src/api/dashboards` timeseries refs
- `src/scripts/utilities/kill_bots.py` / `debug_cursor_location.py` / `hello_world.py`
- `src/web/job_insight.html` / `jobs_page.html`
- `src/web/js/app_shell.js` / `chat_page.js` — **partial**
- `start_cuttle.sh`

## Next session (do not restart Phase 1)

1. Promote `src/api/*.py` structure → execution (caller greps). Skip files already listed above.
2. `pipeline-execution-finish` JS/HTTP callers (audit A14).
3. `bot_config.json` cwd at daemon vs Discord vs tests.
4. Optional empty-clone: README daemon path only (not Flask-alone as currently written).
5. Android which-APK.

Update the inventory **review** column when promoting; keep path list stable.
