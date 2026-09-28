# Cuttle architecture map (repository-wide)

**Date:** 2026-09-27 (continued) · **HEAD:** `4f2880c` · **Code:** not modified. Inventory review status: [`../reviews/repository-inventory.md`](../reviews/repository-inventory.md).

Companion: [`docs/guides/WEB_CHAT_API.md`](../guides/WEB_CHAT_API.md) (Flask composition root only), [`docs/guides/MODULARITY.md`](../guides/MODULARITY.md), this audit’s [`../reviews/repository-audit.md`](../reviews/repository-audit.md).

This map describes **what exists and how it boots**, not a mandate to extract `web_chat_api.py`.

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

**Flask-alone:** `python src/api/web_chat_api.py` (repo root; same as the daemon child). Do not use `src/scripts/start_api_server.py` (different Flask on **:5000**, `time_series_api.py`).

**Do not use** `src/scripts/launchers/start_web_chat.py` (or the other three files in that folder): they `chdir`/`sys.path` to `launchers/` and import `web_chat_api.py` / `project_manager` from the wrong directory. **Confirmed broken** by source, not by running.

**Legacy parallel launcher:** `src/launcher.py` still starts bot+API **from `src/`** (`api/web_chat_api.py`) without the daemon restart protocol — overlapping, not the supported path.

---

## Process and HTTP composition root

`src/api/web_chat_api.py` owns `Flask app`, HTML routes, chat-turn HTTP, many leftover graph/process routes, TLS helper, and **registers**:

| Blueprint / register | Package |
|---|---|
| `auth_bp` | `api.auth_api` |
| `workers_bp` | `api.device_workers.routes` |
| `dashboards_bp` | `api.dashboards.routes` |
| widgets, TTS, terminal, Electron, Android APK | `register_*_routes(app)` |

**Bottleneck:** anything that needs live status, harness cwd, or assistant metadata often **lazy-imports** `web_chat_api` (`chat_delivery`, `auth_api`, `agent_router.dispatch`, `subagents`, `kernel._default_chat_cwd`).

Daemon liveness is `GET /api/status`. `GET /api/health` is a heavier diagnostic (CLI probes) — do not confuse them.

---

## Chat turn (web)

```
User (web)
  → POST /api/chat
  → sticky/starred slash, vision pre-pass
  → native /restart
  → process_message_with_bot → harness kernel / agent router
  → SSE + persist + action-form rewrite
```

**Discord inbound chat gateway:** retired (2026-09). See [`extension-boundaries.md`](extension-boundaries.md) and [`../reviews/discord-cleanup-2026-09.md`](../reviews/discord-cleanup-2026-09.md).

**Discord channel read/post (keep; no gateway required):** `python -m api.discord_cli` (REST GET) and `discord.post` (REST POST via `api.discord_ops` / `project_actions`). Token from `src/.env` does not start a gateway. Per-project aliases: `{project}/.cuttle/actions/discord-post.yaml`. Arbitrary snowflakes cannot bypass the post allowlist.

**Live turn:** `api.chat_delivery` + `api.chat_run_registry` + `/api/chat-steer` / `/api/chat-cancel`.

**History:** `api.auth_db` SQLite (`src/data/db/`, gitignored). Pairing store for **users** is separate from **worker** enroll.

---

## Agent subsystem

| Piece | Location |
|---|---|
| Catalog / adapters | `src/api/agent_harness/agents/*` |
| Kernel | `src/api/agent_harness/kernel.py` |
| Router | `src/api/agent_router/` |
| CLI wrappers | `src/scripts/utilities/*_cli_tool.py` |
| Project commands/actions | `{project}/.cuttle/` + hub `.cuttle/` |
| Brain / context compile | `src/api/cuttle_brain/` |

Graph-era **HTTP** `POST /api/execute-tool` still wraps `_execute_remote_agent_tool`, which internally calls `_run_harness_web_command`. No first-party JS caller of `/api/execute-tool` was found. Chat does **not** go through that HTTP route.

---

## Workers mesh

Coordinator HTTP: `/api/workers/*` (`device_workers`). Store: gitignored SQLite via `CUTTLE_DEVICE_WORKERS_DB` / default path. Enroll is LAN/RFC1918 + setting (see GitHub #1). Runtime claim/complete still loopback-friendly.

---

## AuthZ

`api.http_authz` (owner / session / UI operator). `api.auth_api` sessions, OAuth, guest. Worker tokens in `device_workers.auth`. Action forms HMAC in `project_actions`.

---

## Settings

`src/managers/settings_manager.py` → `src/settings.json` (**gitignored** `*.json`). Defaults created if absent. HTTP `/api/settings/*` still on `web_chat_api`. `bot_config.json` duplicated at repo root, `src/`, `electron/`, tests — `src/core/config.py` looks for `bot_config.json` relative cwd (**uncertain** which copy wins).

---

## Persistence

| Store | Tracked? |
|---|---|
| `cuttle_auth.db` chat/auth | ignored |
| HMAC secret file | ignored |
| Certs | ignored |
| Pairing JSON | likely ignored `*.json` |
| Query logs / uploads | `src/output`, `src/web/logs` ignored or generated |

---

## Frontend

Vanilla JS: `src/web/js/app_shell.js` (shell), `chat_page.js` (chat). No bundler. Electron loads the same HTTPS origin.

---

## CLI / automation

Canonical: `python -m api.*` (`.cuttle/docs/agent-ops-cli.md`). Hub actions: `.cuttle/actions/*.yaml`. POSIX launchers: `.cuttle/scripts/*.sh`. Windows `.bat`/`.ps1` still present for mixed hosts.

---

## Legacy / superseded (see audit)

Graph JSON pipelines and the node editor are **retired**. Jobs live path: `/api/cuttle-jobs` + workers. Graph `fetch` leftovers remain in `jobs_page.html` / `job_insight.html` until stream 1. Telegram/Slack HTTP is **unwanted**. Discord REST read/post does **not** use a gateway process. Extension map: [`extension-boundaries.md`](extension-boundaries.md). Historical consumer tables: [`../reviews/graph-discord-consumers.md`](../reviews/graph-discord-consumers.md).

`src/launcher.py` vs daemon. Port-5000 time-series stack. `landing_page_backup.html`. Control panel HTML still linked.

---

## Important invariants

- Never kill Flask from an agent it hosts; use daemon `/restart`.
- Action-form recovery: HMAC on persisted specs (`action_forms` / `project_actions`).
- `project_key = "pc_bot"` still documented as Claude Code project root in `AGENTS.md`.
- Electron sandbox: no automatic `--no-sandbox`.
