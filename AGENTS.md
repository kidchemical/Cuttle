# AGENTS.md — multi-agent brief for the Cuttle repo

Guidance for Cursor, Muse, Codex, Claude Code, Hermes, and other agents working in this repository.

Coding tasks: read this brief, then `docs/architecture/ARCHITECTURE_PRINCIPLES.md` and
`docs/architecture/repository-map.md` for the owning slice before editing.

## Runtime safety

Never force-kill the hosting Flask API or daemon from an agent session.
The daemon owns Flask lifetime; see [action forms](.cuttle_global/docs/action-forms.md)
for restart procedures and [development instance safety](docs/architecture/development-instance-safety.md)
before risky runtime changes. Electron lifetime is owned by the Host; tray Exit
is the explicit daemon shutdown path, while UI relaunch/update preserves it.

## What is Cuttle

Cuttle is a persistent, autonomous AI agent framework. It runs as a system-tray (or headless) daemon that manages a Flask API (default HTTPS port 8080; other listeners in `src/api/server_ports.py`) and vendor agent CLIs. Web chat uses starred slash agents (`/cursor`, …) and the agent router. Discord is **optional agent-ops** (REST read/post), not an inbound gateway.

Project config lives under `.cuttle/` (commands, actions, docs, rules, `GLOBAL.ini` for global-layer selection); shared global config under `.cuttle_global/`. Prefer that over inventing parallel paths. Extension categories: [`docs/architecture/extension-boundaries.md`](docs/architecture/extension-boundaries.md).

## Starting the Project

POSIX, from the repository root:

```bash
./start_cuttle.sh
# Or: .venv/bin/python src/scripts/cuttle_daemon.py
```

Windows PowerShell, from the repository root:

```powershell
.\.venv\Scripts\python.exe src\scripts\cuttle_daemon.py
```

**Runtime state never lives in the install tree.** Every mutable byte (DBs,
settings, uploads, logs, `.env`, secrets, the shared personal overlay) lives in
the per-user Cuttle home, `core.runtime_paths.cuttle_home()`:
`%LOCALAPPDATA%\Cuttle` on Windows, `~/.local/share/cuttle` on POSIX,
`CUTTLE_HOME` to override. Resolve paths through `runtime_paths` helpers; never
join state onto `__file__`. Layout and the one-time move of older checkouts:
[`docs/architecture/cuttle-home.md`](docs/architecture/cuttle-home.md).

**Environment:** optional provider/API keys and Discord REST agent-ops token
live in `<home>/.env`; the daemon loads this before spawning child processes.
File-shaped secrets (TLS cert/key, GitHub App `.pem`, token files) live only in
`<home>/secrets/` via `core.runtime_paths.secrets_dir()` — never a project
`.cuttle/`, `_personal/`, or a new ad-hoc folder.
Vendor CLI authentication is separate (see the agent catalog manifests).

## Development dependencies and tests

POSIX, from the repository root:

```bash
.venv/bin/python -m pip install -r src/requirements/requirements.txt -r src/requirements/requirements-dev.txt
.venv/bin/python -m pytest src/tests/
```

Windows PowerShell, from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r src/requirements/requirements.txt -r src/requirements/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest src/tests/
```

Use the project venv, not system Python. For `python -m api.*` examples, source
path setup is required; see [agent ops](.cuttle_global/docs/agent-ops-cli.md).

## Architecture

### Execution Flow

```
User message (Web Chat / Electron / Android)
  → web_chat_api.py
  → sticky/starred slash agent OR agent router
  → harness CLI (`/cursor`, `/codex`, …) or LLM fallback
```

Chat ingress is transport-agnostic. Do not add platform-specific execution routes (the retired Discord path was `POST /api/pipeline-trigger-discord`).

### Key Layers

| Layer | Path | Role |
|---|---|---|
| Daemon | `src/scripts/cuttle_daemon.py` | Spawns Flask, tray icon, workers |
| Flask API | `src/api/web_chat_api.py` | REST endpoints, web UI, chat dispatch. Map: `docs/guides/WEB_CHAT_API.md` (read before adding imports or routes; do not extract unless product work is blocked). |
| Discord agent-ops | `src/api/discord_ops/`, `src/api/discord_cli/` | Optional REST read + confirmed `discord.post`. Not a chat surface. |
| Cursor Agent CLI | `src/scripts/utilities/cursor_cli_tool.py` | Headless `agent -p` (stream-json, resume) — same adapter pattern as Codex |

### Frontend

The web UI is vanilla JS served by Flask at port 8080. Key files:
- `src/web/app_shell.html` (shell) + `src/web/chat_page.html` — main web chat UI
- `src/web/router_editor.html` — agent router editor

### Settings & Routing

`<home>/config/settings.json` + `src/managers/settings_manager.py` handle starred slash agents and LAN/auth settings.

### Ownership

One subsystem owns each area. Touch the owner, not the monoliths
(`web_chat_api.py`, `chat_page.js`, `app_shell.js` compose; they never own
domain decisions, and owned layers never import `web_chat_api`). Line ceilings
and boundaries are enforced by `src/tests/quality/`. **Before working in a row,
read its full entry (modules, entry interface, tests) in
[`docs/architecture/task-owners.md`](docs/architecture/task-owners.md).**
Building blocks (dropdowns, modals, agent/project pickers, toasts, model lists,
helpers) have one implementation each: look them up in
[`docs/architecture/canonical-components.md`](docs/architecture/canonical-components.md)
and use or extend that owner — never a look-alike variant.

| Task | Owner |
|---|---|
| New chat-page feature | `src/web/js/chat/chat_<domain>.js` (`Cuttle*` + `create(host)`) |
| Inline transcript row | family in `docs/architecture/chat-inline-surfaces.md`; never a hand-rolled row |
| New shell feature | `src/web/js/shell/shell_<domain>.js` (`CuttleShell*`) |
| New backend route family | an `api.<owner>.routes` blueprint, never `@app.route` on `web_chat_api.py` |
| New settings route | `src/api/settings_routes.py` + `managers/settings_manager.py` |
| New Spaces feature | `src/web/js/spaces/*.js` (`CuttleSpaces.*`) |
| New slash command | `src/web/js/chat/chat_slash.js`; project commands `src/api/project_commands.py` |
| Chat execution | `api.chat_coordinator` (`submit_agent_turn` / `submit_agent_stream_turn`) |
| Existing-chat agent ops (send / status / supervise) | `src/api/chat_ops/`; `python -m api.chat_cli` |
| Live work tree (what is running) | `src/api/jobs/`; `python -m api.jobs work-tree` |
| Action-form state | `src/api/action_form_state.py` |
| Vendor CLI behavior / drop-in adapter | `src/api/agent_harness/agents/<id>/` (`manifest.yaml` + `adapter.py`) |
| External service op | `src/api/service_ops/`; Discord owner `src/api/discord_ops/` |
| Knowledge-graph provider | `src/api/knowledge_graph/` |
| Flask/daemon lifetime | daemon owns; `src/api/flask_restart.py` via the `flask.restart` form |
| Background text helper (API or tool-less CLI) | `src/api/background_helpers.py` (one task row + Settings picker) |
| Context envelope | `src/api/cuttle_brain/` (`CONTEXT_COMPILER.md`) |
| New experimental feature | one `FlagSpec` in `src/api/experimental/features.py` |
| Auto-compaction | `src/api/auto_compact/` |
| Gizmos / Tasks | `src/api/gizmos/` |
| IDE | `src/api/ide/` + `src/web/js/ide/` |
| Agent review | `src/api/agent_review/` |
| Secret vault | `src/api/secret_vault/` |
| Achievements | `src/api/achievements/` |
| Orchestration telemetry | `src/api/orchestration_telemetry/` |
| Orchestrator + Agents | `src/api/orchestrator/`; profiles in `api.subagents.profiles` |

### Tools for agents

Cuttle does **not** host an MCP tool server. Guest CLIs keep their own MCP. Cuttle-owned verbs for agents are `python -m api.<module>` (see `.cuttle_global/docs/agent-ops-cli.md`). **`/cursor`** runs `scripts.utilities.cursor_cli_tool` via the Cursor harness adapter. Discord reads use `python -m api.discord_cli`; feature posts use the `discord.post` action. Cuttle ships no bundled general-purpose device integrations: personal or device integrations (lighting, 3D generation, …) belong in their own CLI or in the owning project's `.cuttle/`, not in core.

### Agent Router (execution harness selection)

- **Thesis / sockets:** `docs/guides/MODULARITY.md` — Cuttle is a home-lab harness of harnesses; the router is a socket (cheap LLM, local LLM, or another harness as brain).
- **Guide**: `docs/guides/AGENT_ROUTER.md`
- **Package**: `src/api/agent_router/` — sits above sticky `/cursor`/`/codex`/`/deepseek`/… selection
- **Config**: `settings.json` → `agent_router` (mode `api` default; OpenAI `gpt-4o-mini` routing brain)
- Clean sessions with no sticky agent invoke the router; starred or manually pinned agents bypass it.
- Before changing routing, failure handling, the star, cancellation or the turn guard, read the
  "Behaviour invariants" section of the guide. Mid-turn steering and background detach:
  [`docs/guides/AGENT_STEERING.md`](docs/guides/AGENT_STEERING.md).

### Cuttle chat controls

Restart, push, questions, and chat-handle conventions have single owners:

- Flask restart and question cards: [action-forms.md](.cuttle_global/docs/action-forms.md).
- Git push/undo: [git.md](.cuttle_global/docs/git.md); never push from the agent shell.
- Chat handles/history and panes: [chat-history.md](.cuttle_global/docs/chat-history.md).
  Emit bare CH- handles, never file links.

The native `/restart` palette entry is in `src/web/js/chat/chat_slash.js`
(`CuttleChatSlash.SLASH_COMMANDS`); it is intercepted before agent dispatch.

### Split panes (“1st pane”, “2nd pane”, …)

When the user has multiple chat columns open (viewport split), panes are **left → right** (1st = leftmost).

- The app shell POSTs layout to `POST /api/shell/panes` on every persist.
- Agents can list panes: `GET https://127.0.0.1:8080/api/shell/panes` (self-signed cert).
- Read history: `GET /api/shell/panes/<n>/messages?limit=40` (1-indexed).
- Remote-agent prompts auto-attach a pane map when 2+ panes are open, and **full recent history** when the user names a pane (e.g. “read 1st pane”, “what’s in pane 2”).

### Local providers

Chat runs CLI adapters selected explicitly or by Cuttle Router. There is no
local/cloud/auto chat mode. Configure local inference in the chosen CLI itself.
Local model endpoints can serve the router brain and small completion helpers;
Cuttle connects to user-managed servers and never owns their lifecycle.

## Self Improvement

Record portable lessons in architecture docs or regression tests. Report public bugs and feature requests through GitHub issues. Keep personal workflows in the install-local overlay.

## Important Conventions

- Turn dispatch lives in owned services, not the entry module (`api.agent_harness.runners`,
  `api.chat_turn`, `api.chat_coordinator`, `api.chat_turn_workflow`, `api.chat_turn_persist`,
  `api.chat_turn_row`, `api.chat_turn_id`). What each owns:
  [`docs/architecture/task-owners.md`](docs/architecture/task-owners.md) → Turn dispatch.
  `process_message_with_bot` in `web_chat_api.py` is only the compat entry for `/api/sessions/send`.
- Flask's default HTTPS listener is port **8080** (not 5000); companion/phone ports come from `src/api/server_ports.py`
- Discord token is read from `<home>/.env` — the daemon loads this before child processes; Discord is REST-only, with no inbound bot subprocess
- **Project commands**: `{project}/.cuttle/commands/*.md` (YAML frontmatter + body) appear in the chat `/` palette for that project. See `.cuttle_global/skills/cuttle-project-commands/SKILL.md`. Invoke as `/{name}` or `/cmd {name}` (works after sticky `/cursor` too).

### Chat attachments (images / PDFs / voice clips)

Cuttle sends attachment content to its CLI harnesses as text: `src/api/vision_prepass.py` turns uploads
into a text digest that is **appended** (never prefixed) above every slash-agent
handler; history keeps the short `[Attached: …]` note plus `metadata.attachments`.
Speech to text is one setting (`api.voice_stt`). Details and tests:
[`docs/guides/CHAT_ATTACHMENTS.md`](docs/guides/CHAT_ATTACHMENTS.md).

## Local file references

Use clickable file links appropriate to the current OS and client. On Windows,
use forward slashes in file/editor URLs; on POSIX use the real checkout path.
Chat-handle formatting belongs to `.cuttle_global/rules/01-chat-handles.md`.
