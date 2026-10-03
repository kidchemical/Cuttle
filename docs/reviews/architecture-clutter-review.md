# Architecture clutter review — G1 (worktree temp/architecture-g1 @ fe70e696)

## Removed (proved inert)

`src/web/landing_page_backup.html` deleted. Independent consumer evidence:
- Routes: `web_chat_api.py` serves explicit per-file HTML only
  (`/landing_page.html` :1849, plus app_shell/media/query/test/router/node/
  control/task/jobs/job_insight); no `<path>`/wildcard HTML route.
- Static: Flask `send_from_directory(project_root / 'web', <named file>)`
  per route; scoped `/js /css /img /output /logs` mounts only.
- Startup/navigation/scripts/tests/templates/packaging: zero filename hits
  in `src/ electron/ .cuttle/ start_cuttle.sh` (code suffixes); live owner
  `landing_page.html` untouched.
- Remaining filename hits are historical records only (organization plan,
  repository-map, cleanup-plan, repository-inventory, discord-cleanup,
  repository-audit) — kept and labeled as history, not purged.

## Docstring corrections (no runtime change)

- `src/tests/e2e/test_app_shell_navigation.py`: stale Flask-start pointer
  (`electron && npm start` / `start_api_server.py`) → canonical
  `start_cuttle.sh` / `cuttle_daemon.py` / `src/api/web_chat_api.py`.
- `src/scripts/start_api_server.py`: clarified as optional legacy
  test-analytics entrypoint (:5000), not current chat startup; dropped
  retired node-editor wording.
- `docs/reviews/cleanup-plan.md` step 3.2 marked DONE with proof refs.
- `repository-map.md` legacy paragraph updated (backup moved to removed).

## Kept / deferred with evidence (no fix this slice)

- `start_api_server.py` + `time_series_api.py`: optional legacy HTTP
  (:5000) pair, no product URL client of `/api/timeseries` found. In-process
  `TestHistoryManager.get_time_series_data` stays live via
  `run_tests_with_logging.py:404` and `test_history_manager.py` self/demo
  use — must not be deleted by keyword. Retained pending workflow
  classification.
- Jobs → Job-insight chain (real, do not delete without a behavior fence):
  `jobs_page.html:908` links `/job_insight.html?pipeline=` (served :1916),
  which calls `/api/job-insight` (:2657). Mismatch recorded, not fixed: the
  route reads retired graph-shaped pipeline JSON via SettingsManager
  (`get_pipeline_path`), `retired_pipeline_registry`, and
  `query_data_*.json` logs — not the active harness execution tracking that
  jobs_page lists. Separate product-behavior decision required.
- Required 410 routes, in-process reporters, graph-shaped settings compat,
  active execution tracking: untouched.

## Reproducible checks

```bash
# No code consumer of the removed backup:
rg -l "landing_page_backup" src/ electron/ .cuttle/ start_cuttle.sh \
  -g '*.py' -g '*.js' -g '*.html' -g '*.sh' -g '*.ps1' -g '*.ini' \
  -g '*.json' -g '*.yaml' -g '*.yml'
# → no output
# Explicit routes only (no HTML wildcard):
rg "@app.route" src/api/web_chat_api.py | rg -i "path>|wildcard" || true
# Syntax of touched files:
python3 -m py_compile src/scripts/start_api_server.py \
  src/tests/e2e/test_app_shell_navigation.py
```
