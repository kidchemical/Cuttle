# Cuttle Logging Overview

Canonical reference for all logs, reports, and debugging output in Cuttle.

---

## Quick Reference

| Log Type | Location | Access |
|----------|----------|--------|
| **Query reports** | `src/web/logs/query_report_*.html` | [Query Reports](http://127.0.0.1:8080/query_reports.html) |
| **Pipeline logs** | `src/web/logs/pipeline_log_*.html` | [Launch Reports](http://127.0.0.1:8080/launch_reports.html) |
| **Test reports** | `src/web/logs/test_report_*.html` | [Test Reports](http://127.0.0.1:8080/test_reports.html) |
| **Data reports** | `src/web/logs/data_report_*.html` | [Data Reports](http://127.0.0.1:8080/data_reports.html) |
| **Budget reports** | `src/web/logs/budget_*.html` | [Data Reports](http://127.0.0.1:8080/data_reports.html) |
| **Daemon log** | `~/cuttle_logs/daemon.log` | Always written when daemon runs |
| **Flask log** | `~/cuttle_logs/flask.log` | Always written when Flask runs |
| **Discord log** | `~/cuttle_logs/discord.log` | Always written when Discord bot runs |
| **Nav debug log** | `~/cuttle_nav_debug.log` | Enable via `localStorage.setItem('cuttle_nav_debug','1')` |
| **Net debug overlay / log** | UI overlay + `~/cuttle_net_debug.log` | `localStorage.setItem('cuttle_net_debug','1')` then Ctrl+Shift+D |
| **Pipeline fetch debug** | Browser console + Node Editor console | Enable via `localStorage.setItem('cuttle_pipeline_debug','1')` |
| **Logs directory** | `src/web/logs/` | [http://127.0.0.1:8080/logs/](http://127.0.0.1:8080/logs/) |

---

## Report Types

### 1. Query Reports

**Purpose:** Track LLM calls, token usage, costs, and execution flow for pipeline runs.

**Sources:** Web Chat, Discord bot, Node Editor pipeline runs.

**Files:**
- `query_report_{id}_{timestamp}.html` — HTML report
- `query_data_{id}_{timestamp}.json` — Raw JSON

**Web UI:** [/query_reports.html](http://127.0.0.1:8080/query_reports.html)

**API:** `GET /api/query-reports`

**Generator:** `src/reports/query_report_generator.py`

### 2. Pipeline / Launch Logs

**Purpose:** Execution logs from Node Editor pipeline runs.

**Files:** `pipeline_log_{timestamp}.html`

**Web UI:** [/launch_reports.html](http://127.0.0.1:8080/launch_reports.html)

**API:** `GET /api/launch-reports`

**Generator:** `src/reports/launch_report_generator.py`

### 3. Test Reports

**Purpose:** HTML test run results with pass/fail, assertions, output.

**Files:** `test_report_with_logs_*.html`

**Web UI:** [/test_reports.html](http://127.0.0.1:8080/test_reports.html)

**API:** `GET /api/test-reports`

**Generator:** `src/tests/html_reporter.py`

### 4. Data Reports

**Purpose:** Data analysis, budget summaries, generated charts.

**Files:** `data_report_*.html`, `budget_*.html`

**Web UI:** [/data_reports.html](http://127.0.0.1:8080/data_reports.html)

**API:** `GET /api/generate-data-report`, `GET /api/generate-budget-report`

**Generator:** `src/reports/data_report_generator.py`, `src/reports/budget_report_generator.py`

---

## Debug Logs

### Nav Debug Log

**Purpose:** Trace navigation state (iframe URLs, state/page sync) for diagnosing viewport freeze issues.

**File:** `~/cuttle_nav_debug.log` (user home directory)

**Enable:** In browser console on app shell: `localStorage.setItem('cuttle_nav_debug', '1')`, then reload.

**API:** `POST /api/debug-log` — app shell posts nav events when enabled.

**Disable:** `localStorage.removeItem('cuttle_nav_debug')`

### Net Debug (Electron pool / slow UI)

**Purpose:** Live insight into Chromium connection pressure — in-flight `fetch` count, slow API calls, chat SSE hold/detach, chat-load timing. Use when Electron wedges or refresh feels stuck while Chrome is fine.

**Enable (client, no rebuild):**
```js
localStorage.setItem('cuttle_net_debug', '1');
location.reload();
```
Tracking starts in the background. **Ctrl+Shift+D** shows/hides the corner overlay (it does not auto-open on refresh unless you left it open). Double-click the overlay to hide.

**Console helpers:**
- `CuttleNetDebug.snapshot()` — current counters / recent calls
- `CuttleNetDebug.event('note', '…')` — manual breadcrumb

**File:** `~/cuttle_net_debug.log` (when overlay/events post via `/api/debug-log` with `channel: "net"`)

**API:** `GET /api/net-insight` — busy chats, pending results, live status (needs Flask restart once after deploy)

**Flask slow lines:** after Flask restart, requests ≥800ms log as `[SLOW] …` in `~/cuttle_logs/flask.log`. Override with env `CUTTLE_SLOW_REQUEST_MS` (set `0` to disable).

**Disable:** `localStorage.removeItem('cuttle_net_debug')` then reload.

### Pipeline Fetch Debug (for "Failed to fetch" errors)

**Purpose:** Log every API request (URL, method, HTTP status or error) when pipelines run. Use this to diagnose "Failed to fetch" when running play pipeline, Run Now, etc.

**Enable:** In browser console (Node Editor or any Cuttle page): `localStorage.setItem('cuttle_pipeline_debug', '1')`, then run a pipeline.

**Output:** Each fetch appears in the browser DevTools console and in the Node Editor console panel, e.g. `[PIPELINE FETCH 12:34:56.789] POST http://127.0.0.1:8080/api/pipeline-execution-start → HTTP 200` or `→ ERROR: Failed to fetch`.

**Disable:** `localStorage.removeItem('cuttle_pipeline_debug')`

**Typical causes of "Failed to fetch":**
- Cuttle daemon/Flask not running — start with `cuttle_daemon.py`
- Wrong port — Flask runs on 8080
- Page opened from `file://` or different origin than API
- Firewall or antivirus blocking localhost
- Server crashed — check `~/cuttle_logs/flask.log` for errors

---

## Node Editor Console Logs

**Purpose:** Interactive execution logs in the Node Editor with node highlighting.

**Location:** In-app console panel (Node Editor).

**Features:**
- Node ID badges when eye icon is enabled
- Click log entry → centers view on node, opens properties
- Hover → node highlights
- Query report links after pipeline run

**Docs:** [PARALLELIZATION_AND_LOG_FEATURES.md](development/PARALLELIZATION_AND_LOG_FEATURES.md)

---

## Process Output (Daemon-Managed Logs)

| Process | Log file | Contents |
|---------|----------|----------|
| **Daemon** | `~/cuttle_logs/daemon.log` | Daemon startup, health checks, restarts, tray actions |
| **Flask** | `~/cuttle_logs/flask.log` | Flask stdout + stderr (requests, errors, pipeline logs) |
| **Discord bot** | `~/cuttle_logs/discord.log` | Bot stdout + stderr (connection, events, errors) |
| **Electron** | Terminal (npm start) | Electron/Node output only |

All daemon-managed logs are in **`~/cuttle_logs/`** (user home: `%USERPROFILE%\cuttle_logs` on Windows).

---

## File Locations Summary

```
%USERPROFILE%\
├── cuttle_logs/                     # Daemon-managed process logs
│   ├── daemon.log                   # Daemon stdout/stderr
│   ├── flask.log                    # Flask web server stdout/stderr
│   └── discord.log                  # Discord bot stdout/stderr
└── cuttle_nav_debug.log             # Nav debug (when enabled)

Pipeline fetch debug:                # In-memory, logs to browser + Node Editor console (when enabled)
  localStorage: cuttle_pipeline_debug = '1'

/path/to/Cuttle/src\web\logs/          # Generated reports (HTML, JSON)
├── query_report_*.html
├── query_data_*.json
├── pipeline_log_*.html
├── test_report_*.html
├── data_report_*.html
└── budget_*.html
```

---

## UI Debugging with Browser Automation

**Purpose:** Reproduce and debug web UI issues by testing in a real browser. Use this when logging and code inspection aren't enough — especially for "button does nothing," "Enter doesn't send," or other interaction failures.

**When to use:**
- UI bugs reported by users (Send button, Enter key, dropdowns stuck)
- Before/after applying fixes to verify they work
- Regression checks for critical user flows (chat, Node Editor, Settings)

**Why browser testing helps:**
- **Logging** only helps when code runs; parse-time errors (e.g. invalid regex) prevent the script from loading, so no logs are emitted
- **Code inspection** can miss runtime errors, iframe context, or timing issues
- **Live browser** confirms the actual behavior and surfaces console errors (e.g. `Uncaught SyntaxError: Invalid regular expression flags`)

**Workflow (Playwright / cursor-ide-browser MCP):**
1. Ensure daemon/Flask is running (port 8080)
2. Navigate to `http://localhost:8080` (or `/chat_page.html` directly)
3. Take `browser_snapshot` to see the page structure and verify it loaded
4. Perform the user action (type in input, press Enter, click Send)
5. Verify expected outcome (message sent, dropdown populated) or capture console errors
6. If fixing code: repeat after the fix to confirm it works

**Cuttle-specific notes:**
- App shell loads pages in an iframe; if debugging, select the `contentFrame` in DevTools console to see iframe logs
- Chat page logs use `[Cuttle Chat]` prefix; API logs use `[API]` in Flask stdout
- For "Loading projects..." stuck: check `/api/projects` response (503 = project_manager unavailable)
- For Enter/Send not working: check console for parse errors or `window.autoResizeWelcomeTextarea is not a function` (indicates script failed to load)

**Prefer browser testing over code-only debugging for UI issues.**

---

## Related Documentation

- [NODE_EDITOR_QUERY_REPORTS.md](development/NODE_EDITOR_QUERY_REPORTS.md) — Query report integration
- [PARALLELIZATION_AND_LOG_FEATURES.md](development/PARALLELIZATION_AND_LOG_FEATURES.md) — Node Editor logs
- [PROJECT_ORGANIZATION.md](PROJECT_ORGANIZATION.md) — Directory structure
