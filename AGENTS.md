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

Cuttle is a persistent, autonomous AI agent framework. It runs as a system-tray (or headless) daemon that manages a Flask API (port 8080) and vendor agent CLIs. Web chat uses starred slash agents (`/cursor`, …) and the agent router. Discord is **optional agent-ops** (REST read/post), not an inbound gateway.

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

**Environment:** optional provider/API keys and Discord REST agent-ops token
live in `src/.env`; the daemon loads this before spawning child processes.
File-shaped secrets (TLS cert/key, GitHub App `.pem`, token files) live only in
`.cuttle/personal/secrets/` via `core.runtime_paths.secrets_dir()` — never
`src/data/`, `_personal/`, or a new ad-hoc folder. Exception: an existing
`src/data/db/action_hmac_secret` remains authoritative until the guarded offline
migration, so previously signed action cards stay valid; new installations use
`secrets_dir()` for it.
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

`src/settings.json` + `src/managers/settings_manager.py` handle starred slash agents, sandbox mode, and LAN/auth settings.

### Ownership (stabilization, Phase 7)

One subsystem owns each area below. Touch the owner, not the monoliths.
Enforced by `src/tests/test_architecture_boundaries.py` (no reverse imports
into `web_chat_api` except the doctor probe and — narrow dev-composition
exception — the spawned shadow child bootstrap `src/scripts/cuttle_shadow_app.py`
(recorded in `REVERSE_IMPORT_ALLOWLIST`; parent-side `api.dev_instance` never
imports the monolith); acyclic `src/api`; coordinator
funnel + SSE pump proven at runtime; manifest schema).

| Task | Owner (code) | Entry interface | Tests |
|---|---|---|---|
| New settings route | `src/api/settings_routes.py` + `managers/settings_manager.py` (never `web_chat_api.py`) | HTTP route + `get_settings_manager()` | settings suites |
| New Spaces feature | `src/web/js/spaces/*.js` (`CuttleSpaces.*`, pure logic, injected storage); shell owns singleton/DOM | `CuttleSpaces.*` namespace | spaces suites |
| New slash command | client registry `src/web/js/chat_slash.js` (`CuttleChatSlash.SLASH_COMMANDS`); project commands `src/api/project_commands.py`; sticky `src/api/starred_slash.py` | registry entry, not a new trigger | slash suites |
| Chat execution | one shared executor; sync turns via `api.chat_coordinator.submit_agent_turn` (`run_harness`, `/api/sessions/send` unclaimed), stream agent turns via `api.chat_coordinator.submit_agent_stream_turn` (SSE framing/live-status stays route transport; pipeline arms owned with never-None no-LLM fallback, compat unclaimed) | `AgentTurnIO`/`StreamTurnIO` + `PreparedAgentTurn` | coordinator + seam + boundary + P5-E/P5-F oracle suites |
| Vendor CLI behavior | `src/api/agent_harness/agents/<id>/` via catalog; kernel coordinates, never installs (BYO-CLI guidance only) | `build_adapter()` / `Adapter` | harness suites |
| Project drop-in adapter | catalog contract: opt-in, validate-before-import, relative-only on-demand namespaced load | `manifest.yaml` + `adapter.py` | `test_harness_project_adapters.py` |
| External service op | `src/api/discord_ops/` pattern (`discord.post`); never branches in project actions, never a chat surface | `python -m api.discord_cli` | discord suites |
| Flask/daemon lifetime | daemon owns; Flask only via `/restart` → `src/api/flask_restart.py` | `flask.restart` action form | restart suites |
| New experimental feature | one `FlagSpec` row in `src/api/experimental/features.py`; gates call `api.experimental.is_enabled()` (settings key `experimental_flags`, kill switch `CUTTLE_EXPERIMENTAL=0`); Settings → Experimental tab renders from the registry | `is_enabled(id)` / `python -m api.experimental list` | `test_experimental_flags.py` |
| Achievements (experimental) | `src/api/achievements/` — `catalog` (vocabulary) / `evaluator` (reads `router_outcomes` + `cuttle_auth`, excludes sub-agent child sessions) / `store` (SQLite state) / `unlocks` / `routes`; client `CuttleAchievements.*` + `CuttleCelebrate.*` | `on_turn_saved()` seam in `chat_turn_persist`; `python -m api.achievements list\|scan\|progress` | `test_achievements.py`, `test_achievements_js.py` |

Direction rule: owned layers (`agent_harness`, `agent_router`,
`chat_coordinator`, `chat_turn*`, `chat_delivery`, `discord_ops`, …) must
never import `web_chat_api`. `web_chat_api.py` (routes/auth/SSE/lanes over
owned services), `chat_page.js` (page orchestration over `CuttleChat*`
slice modules), and `app_shell.js` (shell over `CuttleSpaces.*` + apps)
compose; they do not own domain decisions. Details:
`docs/architecture/extension-boundaries.md`.

### Tools for agents

Cuttle does **not** host an MCP tool server. Guest CLIs keep their own MCP. Cuttle-owned verbs for agents are `python -m api.<module>` (see `.cuttle_global/docs/agent-ops-cli.md`). **`/cursor`** runs `scripts.utilities.cursor_cli_tool` via the Cursor harness adapter. Discord feature posts use `discord.post` + `python -m api.discord_cli`. Live `src/tools/` is ComfyUI, Govee, OCR (chat vision fallback), and web search.

### Agent Router (execution harness selection)

- **Thesis / sockets:** `docs/guides/MODULARITY.md` — Cuttle is a home-lab harness of harnesses; the router is a socket (cheap LLM, local LLM, or another harness as brain).
- **Guide**: `docs/guides/AGENT_ROUTER.md`
- **Package**: `src/api/agent_router/` — sits above sticky `/cursor`/`/codex`/`/deepseek`/… selection
- **Config**: `settings.json` → `agent_router` (mode `api` default; OpenAI `gpt-4o-mini` routing brain)
- Clean sessions with no sticky agent invoke the router; starred/manual sticky agents bypass it
- **Classify, then pick**: `agent_router/classify.py` labels the turn (kind of work: chat/explain/coding/debugging/architecture/research/writing/ops; scope: low/medium/high) and skips the routing brain when confident; the `use_cases` table picks the harness per lane; `agent_router/quota.py` keeps out-of-usage accounts at the back of the chain across turns; `agent_router/budget.py` (opt-in `agent_router.budget.enabled`) does the same up front from live `/usage` plan windows. Chain targets may carry `effort` (manifest-validated, passed as the harness reasoning effort). Tests: `src/tests/test_agent_router_classify.py`, `src/tests/test_agent_router_budget.py`.
- The star is applied **server-side** in `/api/chat` (`starred_slash.apply_default_sticky_prefix`), so a first turn sent without the client chip (LAN client, API caller) still runs the starred agent instead of falling through to the router. Star seeds new chats only; an existing chat follows its own history. Local mode skips cloud-CLI stars.
- Removing the agent badge (chip ✕) is an explicit "route this myself": the composer sends `sticky_agent: "none"`, which beats both the star and the chat's earlier `/cursor` turns, and the removal is remembered in session prefs (`stickyCleared`) so a refresh does not infer the badge back from history. Tests: `src/tests/test_starred_agent_removal.py`.
- **Task failure is terminal** (the agent ran and reported failure): the reply shows its output plus `/retry` hints; the router never silently reruns it on another model. Only "never ran" failures (quota/usage, auth, missing CLI, connection) walk the fallback chain; a quota error skips that agent's other premium models for the turn (Cursor → `auto` first). Tests: `src/tests/test_agent_router.py`.
- **Cancellation is terminal** (`FailureKind.CANCELLED`): a `[CANCELLED]` result from Stop / chat deletion never escalates and never spawns a fallback. Pressing Stop used to buy a Grok escalation plus a Codex run that outlived the turn.
- **Mid-turn steering** (`src/api/agent_harness/steer.py`): Codex turns run on `codex app-server` and Muse Code turns on `muse serve`, so a follow-up sent while they work goes into the live turn (`turn/steer`) through `POST /api/chat-steer` instead of waiting in the follow-up queue. The composer tries steer first and queues on `steered: false` (no steerable run, other slash command, attachments, or turn already finished). The server persists the steered user row (`metadata.steered`). Either runner returns `fallback` if it can't start a turn, and the adapter reruns on `exec`. Off switch: `settings.json` → `agent_steer: {"codex": false}` or env `CUTTLE_AGENT_STEER=0`. Codex reads steer input only at its next sampling step: the status strip shows `steer queued: …`, and a steer the turn never echoed back is listed in the reply (`undelivered_steers`) instead of vanishing. While a Codex turn is live, `/api/agent-context` uses the runner's token snapshot and never resumes the busy thread from a second app-server. Claude Code turns run `claude -p --input-format stream-json --replay-user-messages` with stdin open: a steer is another stdin user line, folded in at the next tool boundary (or answered as an extra turn after the first `result`, merged into one reply); stdin closes at the first root `result`. Off switch `agent_steer: {"claude": false}` restores the plain one-shot `-p`. Cursor `-p` has no mid-turn input channel. Tests: `src/tests/test_agent_steer.py`.
- **Turn guard**: `chat_delivery` hands each turn a token (`current_turn` / `is_stale_turn`). A run whose turn was superseded (Stop → re-send) cannot persist its reply, park it for the client, release the live turn's busy lock, or start another routed target.

### Cuttle chat controls

Restart, push, questions, and chat-handle conventions have single owners:

- Flask restart and question cards: [action-forms.md](.cuttle_global/docs/action-forms.md).
- Git push/undo: [git.md](.cuttle_global/docs/git.md); never push from the agent shell.
- Chat handles/history and panes: [chat-history.md](.cuttle_global/docs/chat-history.md).
  Emit bare CH- handles, never file links.

The native `/restart` palette entry is in `src/web/js/chat_slash.js`
(`CuttleChatSlash.SLASH_COMMANDS`); it is intercepted before agent dispatch.

### Split panes (“1st pane”, “2nd pane”, …)

When the user has multiple chat columns open (viewport split), panes are **left → right** (1st = leftmost).

- The app shell POSTs layout to `POST /api/shell/panes` on every persist.
- Agents can list panes: `GET https://127.0.0.1:8080/api/shell/panes` (self-signed cert).
- Read history: `GET /api/shell/panes/<n>/messages?limit=40` (1-indexed).
- Remote-agent prompts auto-attach a pane map when 2+ panes are open, and **full recent history** when the user names a pane (e.g. “read 1st pane”, “what’s in pane 2”).

### Local LLM (Ollama)

- **Queuing**: Ollama processes one request at a time per model. Cuttle serializes calls to `llm-local` (Ollama) with a lock in `web_chat_api.py` (`_ollama_request_lock`). When multiple requests hit Ollama at once, later requests wait; the UI shows **"Waiting for local LLM (Ollama)..."** and then **"Calling local LLM (Ollama)..."**.
- **Query reports**: Each Ollama LLM call in the query log includes a note: either that the request waited for a prior one, or that local LLM requests are serialized.
- **Chat statuses**: `/api/llm-request` (when `sessionId` is in the payload) emits status updates (e.g. "Calling local LLM (Ollama)...") via `emit_chat_status(session_id, message)`.

## Self Improvement

Scheduled `Self_Improvement.json` was removed with the graphs. Process backlog lives in `.cuttle/learnings/` (`LEARNINGS.md`, `ERRORS.md`, `FEATURE_REQUESTS.md`); use structured entries (`[LRN-YYYYMMDD-XXX]`, `[ERR-...]`, `[FEAT-...]`) with Priority, Status, Area, Metadata. Promote broadly applicable learnings to `AGENTS.md`. Not Context Compiler `docs/` — open on demand only.

## Important Conventions

- Turn dispatch lives in owned services, not the entry module: `api.agent_harness.runners` (all harness CLI entries), `api.chat_turn` (request/selection seam), `api.chat_coordinator` (shared transport-neutral submit: `PreparedAgentTurn`/`select_agent_turn`/`submit_agent_turn`; route lanes submit claimed, legacy surfaces unclaimed), `api.chat_turn_workflow` (lane orchestration), `api.chat_turn_persist` (saver/user persist). `process_message_with_bot` in `web_chat_api.py` is only the compat entry for local-mode prompts and `/api/sessions/send`.
- Flask runs on port **8080** (not 5000)
- Discord token is read from `src/.env` — the daemon loads this before child processes; Discord is REST-only, with no inbound bot subprocess
- **Project commands**: `{project}/.cuttle/commands/*.md` (YAML frontmatter + body) appear in the chat `/` palette for that project. See `.cuttle_global/skills/cuttle-project-commands/SKILL.md`. Invoke as `/{name}` or `/cmd {name}` (works after sticky `/cursor` too).

### Chat attachments (images / PDFs)

- No CLI harness (Cursor, Codex, Muse, Hermes) accepts image input. `src/api/vision_prepass.py` turns uploads into a text digest: **Claude vision → OpenAI vision → OCR**, and reports the provider error inline rather than degrading into a silent "(no text found)" — otherwise the agent confidently says no image arrived.
- The pre-pass runs in `/api/chat` **above every slash-agent handler** (`_run_attachment_prepass`, just after the starred sticky prefix). It used to run at the bottom, so `/cursor`-badged turns reached the CLI with no description and persisted no attachment metadata.
- The digest is **appended**, never prefixed — each handler matches `^/cursor`/`^/muse`/… against `message_content` and slices the prompt off the front.
- History stores the short `[Attached: …]` note plus `metadata.attachments` (`filename`/`mime`/`url`), not the digest (`_attachment_history_text`). That metadata is what re-renders thumbnails after a refresh; without it the image disappears.
- Uploads land in `src/output/uploads/{session}/`, served via `/output/…`. `resolve_upload_refs` rejects any client path outside that root.
- Tests: `src/tests/test_chat_attachments.py`.

## Local file references

Use clickable file links appropriate to the current OS and client. On Windows,
use forward slashes in file/editor URLs; on POSIX use the real checkout path.
Chat-handle formatting belongs to `.cuttle_global/rules/01-chat-handles.md`.
