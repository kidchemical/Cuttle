# AGENTS.md — multi-agent brief for the Cuttle repo

Guidance for Cursor, Muse, Codex, Claude Code, Hermes, and other agents working in this repository.

## Flask / daemon restart safety

Cuttle’s Flask API is a child of `cuttle_daemon`.

- **Never** use `taskkill` / `Stop-Process` on `web_chat_api` or `cuttle_daemon` to restart them from an agent session.
- Use `/restart graceful`, `/restart when-idle`, or `/restart force --yes` (or `POST /api/flask/restart`).
- In Cuttle chat, propose the restart with the `flask.restart` action form (clickable choice card) rather than asking the user to type a slash command; the slash commands are the fallback.
- Killing Flask from inside a chat destroys the delivery path for your own reply.

Unrelated `taskkill` of non-Cuttle processes (build tools, test servers you started, etc.) is allowed.

Electron Host exit: only tray **Exit** may stop the daemon it spawned, and it asks first when agent turns are running (`requestHostExit` in `electron/main.js`). SIGTERM/SIGINT, updates, and relaunches close the UI only — the daemon and in-flight turns survive and the next launch reconnects.

Canonical daemon restart (admin / external terminal only):
- Windows: `.cuttle/scripts/restart-daemon.ps1` from the repo root
- Linux / macOS: `.cuttle/scripts/restart-daemon.sh` (or `./start_cuttle.sh` after a stop)

Global action forms (`flask.restart`, workers, …) use `run:` (PowerShell) on Windows and `run_posix:` (venv python / bash) on Linux. Do not invoke `.ps1` recipes from Ubuntu.

Details: `.cuttle_global/docs/action-forms.md` (Flask restart), `src/api/flask_restart.py`, `src/api/restart_safety_policy.py`.

## What is Cuttle

Cuttle is a persistent, autonomous AI agent framework. It runs as a system-tray (or headless) daemon that manages a Flask API (port 8080) and vendor agent CLIs. Web chat uses starred slash agents (`/cursor`, …) and the agent router. Discord is **optional agent-ops** (REST read/post), not an inbound gateway.

Project config lives under `.cuttle/` (commands, actions, docs, rules, `GLOBAL.ini` for global-layer selection); shared global config under `.cuttle_global/`. Prefer that over inventing parallel paths. Extension categories: [`docs/architecture/extension-boundaries.md`](docs/architecture/extension-boundaries.md).

## Starting the Project

```bash
# Linux / macOS (repo root)
./start_cuttle.sh
# or: .venv/bin/python src/scripts/cuttle_daemon.py

# Windows
# cd \path\to\Cuttle
# .venv\Scripts\python.exe src\scripts\cuttle_daemon.py
```

**Venv**: use `.venv/bin/python` (POSIX) or `.venv\Scripts\python.exe` (Windows).

**Environment**: secrets (optional `DISCORD_TOKEN` for REST agent-ops, API keys) live in `src/.env`. The daemon loads this file before spawning subprocesses. Setting `DISCORD_TOKEN` does **not** start an inbound Discord gateway.

## Running Tests

```bash
.venv/bin/python -m pytest src/tests/
# Windows: .venv\Scripts\python.exe -m pytest src/tests/
```

## Installing Dependencies

```bash
.venv/bin/pip install -r src/requirements/requirements.txt
# Windows: .venv\Scripts\pip.exe install -r src/requirements/requirements.txt
```

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
- `src/web/landing_page.html` / chat shell — main web chat UI
- `src/web/router_editor.html` — agent router editor

### Settings & Routing

`src/settings.json` + `src/managers/settings_manager.py` handle starred slash agents, sandbox mode, and LAN/auth settings.

### Ownership (stabilization, Phase 7)

One subsystem owns each area below. Touch the owner, not the monoliths.
Enforced by `src/tests/test_architecture_boundaries.py` (no reverse imports
into `web_chat_api` except the doctor probe; acyclic `src/api`; coordinator
funnel + SSE pump proven at runtime; manifest schema).

| Task | Owner (code) | Entry interface | Tests |
|---|---|---|---|
| New settings route | `src/api/settings_routes.py` + `managers/settings_manager.py` (never `web_chat_api.py`) | HTTP route + `get_settings_manager()` | settings suites |
| New Spaces feature | `src/web/js/spaces/*.js` (`CuttleSpaces.*`, pure logic, injected storage); shell owns singleton/DOM | `CuttleSpaces.*` namespace | spaces suites |
| New slash command | client registry `src/web/js/chat_slash.js` (`CuttleChatSlash.SLASH_COMMANDS`); project commands `src/api/project_commands.py`; sticky `src/api/starred_slash.py` | registry entry, not a new trigger | slash suites |
| Chat execution | one shared executor; sync turns via `api.chat_coordinator.submit_agent_turn` (`run_harness`, `/api/sessions/send` unclaimed), stream turns via `_generate_chat_stream` with the same run fn over the same delivery primitives | `AgentTurnIO` + `PreparedAgentTurn` | coordinator + seam + boundary suites |
| Vendor CLI behavior | `src/api/agent_harness/agents/<id>/` via catalog; kernel coordinates, never installs (BYO-CLI guidance only) | `build_adapter()` / `Adapter` | harness suites |
| Project drop-in adapter | catalog contract: opt-in, validate-before-import, relative-only on-demand namespaced load | `manifest.yaml` + `adapter.py` | `test_harness_project_adapters.py` |
| External service op | `src/api/discord_ops/` pattern (`discord.post`); never branches in project actions, never a chat surface | `python -m api.discord_cli` | discord suites |
| Flask/daemon lifetime | daemon owns; Flask only via `/restart` → `src/api/flask_restart.py` | `flask.restart` action form | restart suites |

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
- The star is applied **server-side** in `/api/chat` (`starred_slash.apply_default_sticky_prefix`), so a first turn sent without the client chip (LAN client, API caller) still runs the starred agent instead of falling through to the router. Star seeds new chats only; an existing chat follows its own history. Local mode skips cloud-CLI stars.
- Removing the agent badge (chip ✕) is an explicit "route this myself": the composer sends `sticky_agent: "none"`, which beats both the star and the chat's earlier `/cursor` turns, and the removal is remembered in session prefs (`stickyCleared`) so a refresh does not infer the badge back from history. Tests: `src/tests/test_starred_agent_removal.py`.
- Escalation: Cursor Auto task failure → Cursor Grok 4.6; transport/budget → Codex fallback chain
- **Cancellation is terminal** (`FailureKind.CANCELLED`): a `[CANCELLED]` result from Stop / chat deletion never escalates and never spawns a fallback. Pressing Stop used to buy a Grok escalation plus a Codex run that outlived the turn.
- **Mid-turn steering** (`src/api/agent_harness/steer.py`): Codex turns run on `codex app-server` and Muse Code turns on `muse serve`, so a follow-up sent while they work goes into the live turn (`turn/steer`) through `POST /api/chat-steer` instead of waiting in the follow-up queue. The composer tries steer first and queues on `steered: false` (no steerable run, other slash command, attachments, or turn already finished). The server persists the steered user row (`metadata.steered`). Either runner returns `fallback` if it can't start a turn, and the adapter reruns on `exec`. Off switch: `settings.json` → `agent_steer: {"codex": false}` or env `CUTTLE_AGENT_STEER=0`. Codex reads steer input only at its next sampling step: the status strip shows `steer queued: …`, and a steer the turn never echoed back is listed in the reply (`undelivered_steers`) instead of vanishing. While a Codex turn is live, `/api/agent-context` uses the runner's token snapshot and never resumes the busy thread from a second app-server. Cursor `-p` and Claude `-p` have no mid-turn input channel. Tests: `src/tests/test_agent_steer.py`.
- **Turn guard**: `chat_delivery` hands each turn a token (`current_turn` / `is_stale_turn`). A run whose turn was superseded (Stop → re-send) cannot persist its reply, park it for the client, release the live turn's busy lock, or start another routed target.

### Flask restart (daemon-owned) — expanded

- **Never** `taskkill` `web_chat_api` from a Cursor/Codex/Muse agent hosted by that Flask — the reply is lost.
- When proposing a restart in Cuttle chat, emit the `flask.restart` action form (choice card: status / graceful / when-idle / force / `flask.health`) instead of asking in prose — see `.cuttle_global/docs/action-forms.md` (Flask restart). Card restarts are `silent`: progress shows on the card (spinner → green check), and no ack/completion bubbles are written to the chat.
- Action cards carry the `session_id` of the chat they were rendered in; `/api/action-form/run` runs the action in that chat regardless of what the client thinks is open, so a restart can never land its status in another chat.
- Prefer `/restart status|graceful|when-idle|force --yes` or `POST /api/flask/restart` when action buttons aren't available.
- `/restart` is a **native Cuttle control command**: it is intercepted in `/api/chat` (and in `process_message_with_bot`) before sticky/starred agent prefixing, the router, and any Cursor/Codex/Hermes dispatch, so it never becomes a model turn or an agent job. Palette entry lives in `SLASH_COMMANDS` (`controlCommand: true`) in `chat_page.js`.
- Protocol: Flask persists ack + `restart_id` → writes `cuttle_flask_restart_request.json` → daemon stop/start/health → status file + chat completion.
- **Manual verification 2026-08-16:** `/restart status` → native chip (no agent); `/restart graceful` → ack persisted, auto-reconnect, Flask PID 4856→19048, generation 1→2, health 6798.6 ms, completion delivered without a follow-up message (`restart_id` `f2cb0682…`, status file `healthy`).
- Daemon helper: `.cuttle/scripts/restart-daemon.sh` (POSIX) or `.cuttle/scripts/restart-daemon.ps1` (Windows).
- Drain-first: graceful waits/rejects when busy; `when-idle` schedules; force requires confirm.
- No IDE shell gate ships with this repo (the old Cursor `beforeShellExecution` hook was removed); enforcement is the safety core + daemon-owned restart path.

### Git push (Cuttle chat)

- **Never** `git push` from the agent shell in Cuttle chat (including `--force`).
- The user pushing from the Git pending-changes UI is allowed.
- In chat, emit the `git.push` action form (status / push / don’t) and stop. See `.cuttle_global/docs/git.md`. Do not ask “OK to push?” in prose.

### Asking the user questions (Cuttle chat)

- **Never call the IDE `AskQuestion` tool** when the turn runs through Cuttle (`/cursor`, router, etc.). Headless `agent -p` has no picker, so it returns "skipped" instantly and the user sees nothing.
- Emit a Q&A `<cuttle_action_form>` with `"resume": true` instead (`choice`, `multi` with Submit/Cancel, or `form` with `radio`/`checkboxes` fields), then end the turn. See `.cuttle_global/docs/action-forms.md`.
- **One Q&A card per reply.** Several questions → one `form` card with one field per question and a single Submit. Separate resume cards each start a turn on their own click, so the first answer orphans the rest. `rewrite_action_forms` merges stray multi-card replies into one form as a safety net (`merge_qa_resume_specs`).
- The answer bubble has exactly one writer: the client's resume send (`sendMessage({text})` → `/api/chat`). `/api/action-form/run` returns `injected_user_message` but must not persist it — doing both showed every answer twice. `[form-answers]` lists every field, including unanswered ones.
- Never tell the user they "skipped" a question. `src/api/cursor_question_bridge.py` converts stray AskQuestion calls into a card as a safety net.

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

- `project_key = "pc_bot"` is hardcoded as the Claude Code project root in `web_chat_api.py`
- Turn dispatch lives in owned services, not the entry module: `api.agent_harness.runners` (all harness CLI entries), `api.chat_turn` (request/selection seam), `api.chat_coordinator` (shared transport-neutral submit: `PreparedAgentTurn`/`select_agent_turn`/`submit_agent_turn`; route lanes submit claimed, legacy surfaces unclaimed), `api.chat_turn_workflow` (lane orchestration), `api.chat_turn_persist` (saver/user persist). `process_message_with_bot` in `web_chat_api.py` is only the compat entry for local-mode prompts and `/api/sessions/send`.
- Flask runs on port **8080** (not 5000)
- Discord token is read from `src/.env` — the daemon must load this before spawning bot subprocess
- **Project commands**: `{project}/.cuttle/commands/*.md` (YAML frontmatter + body) appear in the chat `/` palette for that project. See `.cuttle_global/skills/cuttle-project-commands/SKILL.md`. Invoke as `/{name}` or `/cmd {name}` (works after sticky `/cursor` too).

### Chat attachments (images / PDFs)

- No CLI harness (Cursor, Codex, Muse, Hermes) accepts image input. `src/api/vision_prepass.py` turns uploads into a text digest: **Claude vision → OpenAI vision → OCR**, and reports the provider error inline rather than degrading into a silent "(no text found)" — otherwise the agent confidently says no image arrived.
- The pre-pass runs in `/api/chat` **above every slash-agent handler** (`_run_attachment_prepass`, just after the starred sticky prefix). It used to run at the bottom, so `/cursor`-badged turns reached the CLI with no description and persisted no attachment metadata.
- The digest is **appended**, never prefixed — each handler matches `^/cursor`/`^/muse`/… against `message_content` and slices the prompt off the front.
- History stores the short `[Attached: …]` note plus `metadata.attachments` (`filename`/`mime`/`url`), not the digest (`_attachment_history_text`). That metadata is what re-renders thumbnails after a refresh; without it the image disappears.
- Uploads land in `src/output/uploads/{session}/`, served via `/output/…`. `resolve_upload_refs` rejects any client path outside that root.
- Tests: `src/tests/test_chat_attachments.py`.

## Terminal hyperlink rule

Whenever referencing local **files or code locations**:

Always output clickable links compatible with Windows Terminal.

Format file paths as:

file:///C:/path/to/file.ext

Also include VSCode deep links:

vscode://file/C:/path/to/file.ext:line

Rules:
- Never output Windows paths using backslashes
- Always convert to forward slash URLs
- Prefer clickable hyperlinks over plain text paths
- Include both file:// and vscode:// links whenever possible
- **Not for chat handles:** emit bare `CH-000182` / `CH-000182-23` only — never
  `[CH-…](file://…)` or other markdown wrappers (those become file chips). See
  `.cuttle_global/rules/01-chat-handles.md`.
