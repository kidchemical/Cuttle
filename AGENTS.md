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

### Ownership (stabilization, Phase 7)

One subsystem owns each area below. Touch the owner, not the monoliths.
Enforced by `src/tests/quality/test_architecture_boundaries.py` (no reverse imports
into `web_chat_api` except the doctor probe and — narrow dev-composition
exception — the spawned shadow child bootstrap `src/scripts/cuttle_shadow_app.py`
(recorded in `REVERSE_IMPORT_ALLOWLIST`; parent-side `api.dev_instance` never
imports the monolith); acyclic `src/api`; per-owner route delegation
`test_phase7_route_families_delegate_to_owners`; coordinator
funnel + SSE pump proven at runtime; manifest schema),
`src/tests/quality/test_composition_root_growth.py` (line ceilings lowered to
the post-refactor baseline: `chat_page.js` 13,405 / `app_shell.js` 4,182 /
`web_chat_api.py` 5,200) and `src/tests/quality/test_composition_root_delegation.py`
(each frontend owner namespace + a thin composition-root call site).

| Task | Owner (code) | Entry interface | Tests |
|---|---|---|---|
| New chat-page feature | `src/web/js/chat/chat_<domain>.js` owner (pure module + controller with explicit `host`/`deps`); `chat_page.js` only orchestrates | owner namespace (`Cuttle*`) + `create(host)` | owner suite + `test_composition_root_delegation.py` |
| New shell feature | `src/web/js/shell/shell_<domain>.js` owner; `app_shell.js` composes | `CuttleShell*` namespace | `src/tests/shell/` + `test_composition_root_delegation.py` |
| New backend route family | an `api.<owner>.routes` blueprint (never a new `@app.route` on `web_chat_api.py`) | blueprint `register_blueprint` | `test_architecture_boundaries.py` + route suite |
| New settings route | `src/api/settings_routes.py` + `managers/settings_manager.py` (never `web_chat_api.py`) | HTTP route + `get_settings_manager()` | settings suites |
| New Spaces feature | `src/web/js/spaces/*.js` (`CuttleSpaces.*`, pure logic, injected storage); shell owns singleton/DOM | `CuttleSpaces.*` namespace | spaces suites |
| New slash command | client registry `src/web/js/chat/chat_slash.js` (`CuttleChatSlash.SLASH_COMMANDS`); project commands `src/api/project_commands.py`; sticky `src/api/starred_slash.py` | registry entry, not a new trigger | slash suites |
| Chat execution | one shared executor; sync turns via `api.chat_coordinator.submit_agent_turn` (`run_harness`, `/api/sessions/send` unclaimed), stream agent turns via `api.chat_coordinator.submit_agent_stream_turn` (SSE framing/live-status stays route transport; pipeline arms owned with never-None no-LLM fallback, compat unclaimed) | `AgentTurnIO`/`StreamTurnIO` + `PreparedAgentTurn` | coordinator + seam + boundary + P5-E/P5-F oracle suites |
| Existing-chat agent ops (send / status / supervise) | `src/api/chat_ops/` — `service` (verbs) / `store` (outbox + schedules SQLite in home) / `worker` (Flask-owned pump; an uncertain send is checked against the target's history by `chat_op_id` before any resend, so a turn never runs twice) / `rules` + `triggers` (event-driven guard checks: subscribes to `api.session_events`, published on finished tool calls and worktree binds) / `local_auth` (60 s loopback capabilities scoped to one chat + one chat route); turns still run through ordinary `/api/chat` ingress; UI (experimental `chat_supervision`): `routes` + client `CuttleChatSupervisionModel` (pure) / `CuttleChatSupervision` (header chip/badge/queued pills, "Supervise this chat…") + Jobs `chat_op` rows | `python -m api.chat_cli status\|snapshot\|send\|stop\|supervise`; `/api/chat-ops/*` | `src/tests/chat_ops/`, `e2e/test_chat_supervision.py` |
| Action-form state | host-owned index `src/api/action_form_state.py` (one row per session/message/form); `api.chat_attention` reads awaiting-input from it, `api.action_forms.reconcile_restart_forms` terminalizes finished/superseded restart cards from the `/api/flask/restart/status` poll; messages GET exposes `action_forms` | `get_action_form_state()` / `reconcile_restart_forms()` | `test_action_form_state.py` + attention/action-form suites |
| Vendor CLI behavior | `src/api/agent_harness/agents/<id>/` via catalog; kernel coordinates, never installs (BYO-CLI guidance only) | `build_adapter()` / `Adapter` | harness suites |
| Project drop-in adapter | catalog contract: opt-in, validate-before-import, relative-only on-demand namespaced load | `manifest.yaml` + `adapter.py` | `test_harness_project_adapters.py` |
| External service op | `src/api/service_ops/` — `protocol.py` (`ServiceOp`) + `catalog.py` registry/discovery; bundled op `ops/discord.py` wraps the owner `src/api/discord_ops/` (`discord.post`). `project_actions` dispatches by `type` through the registry (never a chat surface; drop-ins via `<home>/personal/service_ops` / `CUTTLE_SERVICE_OPS_DIR`) | `python -m api.service_ops list` / `discord.post` / `python -m api.discord_cli` (reads) | service_ops + discord suites |
| Knowledge-graph provider (3P) | `src/api/knowledge_graph/` — protocol (`protocol.py`) + registry (`catalog.py`) + facade; each engine is a `manifest.yaml` + `adapter.py` (bundled: `providers/graphify/`), discovered validate-before-import like agent adapters; store = `<home>/<store_root>/<project-key>/<checkout-key>/` | `python -m api.knowledge_graph <verb> --provider ID` | `src/tests/knowledge_graph/` + `test_architecture_boundaries.py` |
| Flask/daemon lifetime | daemon owns; Flask only via `/restart` → `src/api/flask_restart.py` | `flask.restart` action form | restart suites |
| New experimental feature | one `FlagSpec` row in `src/api/experimental/features.py`; gates call `api.experimental.is_enabled()` (settings key `experimental_flags`, kill switch `CUTTLE_EXPERIMENTAL=0`); Settings → Experimental tab renders from the registry | `is_enabled(id)` / `python -m api.experimental list` | `test_experimental_flags.py` |
| Gizmos (experimental) | `src/api/gizmos/` — `catalog` (types) / `usage` (normalized plan-usage providers) / `service` (validation, placement) / `store` (SQLite + revision) / `routes`; client `CuttleGizmos.*` (pure) + `CuttleGizmosShell` (docks, drag, popover); Electron pop-outs via `gizmo-popouts-sync` | `python -m api.gizmos`; `/api/gizmos`; Apps → Gizmos | `src/tests/gizmos/` |
| IDE (experimental) | `src/api/ide/` — `service` (workspace allowlist, path confinement, etag writes, search/replace, git content) / `routes`; client `src/web/js/ide/` (`CuttleIdeModel` pure + editor/explorer/search/quick/terminal/chat controllers, composed by `ide_page.js`); AI edits go through the embedded chat's send path | Apps → IDE; diff modal **Editor**; `/api/ide/*` | `src/tests/ide/`, `e2e/test_ide_app.py` |
| Agent review (experimental) | `src/api/agent_review/` — `hunks` (pure diff/keep/undo/fold) / `store` (SQLite in home) / `service` (turn hooks from `kernel` + `activity`, reconcile, Undo writes) / `routes`; IDE client `CuttleIdeReview` + `CuttleIdeReviewModel` (pure), Monaco `setReview`; Git surfaces (Pending Changes, diff modal, commit guard) via `CuttleAgentReview` (`js/git/agent_review_client.js`); surfaces read it, never copy it | `python -m api.agent_review`; `/api/agent-review/*`; IDE inline Keep/Undo | `src/tests/agent_review/`, `ide/test_ide_review_js.py`, `e2e/test_ide_agent_review.py`, `e2e/test_agent_review_git_surfaces.py` |
| Secret vault (experimental) | `src/api/secret_vault/` — `core/` (Cuttle-free: `backends` keyring/command presets, `deliver` env/file/stdin/askpass, `redact`, `askpass`) / `service` (metadata + approvals in home, never values) / `cards` / `routes` (no value-returning route); client `CuttleChatSecretFields` (chat `secret` / `secret_approval` fields) + `CuttleSecrets` (Apps → Secrets) | `python -m api.secret_vault run --askpass/--env/--stdin/--file NAME -- cmd`; `/api/secret-vault/*` | `src/tests/secret_vault/` |
| Achievements (experimental) | `src/api/achievements/` — `catalog` (vocabulary) / `evaluator` (reads `router_outcomes` + `cuttle_auth`, excludes sub-agent child sessions) / `store` (SQLite state) / `unlocks` / `routes`; client `CuttleAchievements.*` + `CuttleCelebrate.*` | `on_turn_saved()` seam in `chat_turn_persist`; `python -m api.achievements list\|scan\|progress` | `test_achievements.py`, `test_achievements_js.py` |
| Orchestrator + Agents (experimental) | `src/api/orchestrator/` — `model` (pure, Cuttle-free: playbook schema v1, brief + supervise args) / `store` (SQLite in home: playbooks, chat bindings) / `service` (profile resolution, flag gate, `brief_block` for the Context Compiler) / `routes`; agent profiles stay in `api.subagents.profiles` (+ `profile_routes`); harness/model/effort pins validated by `agent_harness.agent_models.check_agent_pin` on profile save **and** before any spawn creates a child; client `CuttleOrchestrator` (pure) + Apps → Orchestrator / Apps → Agents | `/orchestrate [@playbook] goal` (feature bundle); `python -m api.orchestrator start\|brief\|supervise-command`; `/api/orchestrator/*`, `/api/agent-profiles/*` | `src/tests/orchestrator/` |

Direction rule: owned layers (`agent_harness`, `agent_router`,
`chat_coordinator`, `chat_turn*`, `chat_delivery`, `discord_ops`, …) must
never import `web_chat_api`. `web_chat_api.py` (routes/auth/SSE/lanes over
owned services), `chat_page.js` (page orchestration over `CuttleChat*`
slice modules: `chat_media`, `chat_tts`, `chat_session`, `chat_history_panel`,
`chat_agent_controls`, `chat_slash_palette`, `chat_context_gauge`,
`chat_stream_transport`, `chat_transcript_nav`, `chat_subagent_launchers`, …),
and `app_shell.js` (shell over `shell_panes` / `shell_spaces` / `shell_rail` /
`shell_electron` / `shell_mobile` + `CuttleSpaces.*` + apps)
compose; they do not own domain decisions. Details:
`docs/architecture/extension-boundaries.md`.

### Tools for agents

Cuttle does **not** host an MCP tool server. Guest CLIs keep their own MCP. Cuttle-owned verbs for agents are `python -m api.<module>` (see `.cuttle_global/docs/agent-ops-cli.md`). **`/cursor`** runs `scripts.utilities.cursor_cli_tool` via the Cursor harness adapter. Discord reads use `python -m api.discord_cli`; feature posts use the `discord.post` action. Cuttle ships no bundled general-purpose device integrations: personal or device integrations (lighting, 3D generation, …) belong in their own CLI or in the owning project's `.cuttle/`, not in core.

### Agent Router (execution harness selection)

- **Thesis / sockets:** `docs/guides/MODULARITY.md` — Cuttle is a home-lab harness of harnesses; the router is a socket (cheap LLM, local LLM, or another harness as brain).
- **Guide**: `docs/guides/AGENT_ROUTER.md`
- **Package**: `src/api/agent_router/` — sits above sticky `/cursor`/`/codex`/`/deepseek`/… selection
- **Config**: `settings.json` → `agent_router` (mode `api` default; OpenAI `gpt-4o-mini` routing brain)
- Clean sessions with no sticky agent invoke the router; starred/manual sticky agents bypass it
- **Classify, then pick**: `agent_router/classify.py` labels the turn (kind of work: chat/explain/coding/debugging/architecture/research/writing/ops; scope: low/medium/high) and skips the routing brain when confident; the `use_cases` table picks the harness per lane; `agent_router/quota.py` keeps out-of-usage accounts at the back of the chain across turns; `agent_router/budget.py` (opt-in `agent_router.budget.enabled`) does the same up front from live `/usage` plan windows. Chain targets may carry `effort` (manifest-validated, passed as the harness reasoning effort). Tests: `src/tests/router/test_agent_router_classify.py`, `src/tests/router/test_agent_router_budget.py`.
- The star is applied **server-side** in `/api/chat` (`starred_slash.apply_default_sticky_prefix`), so a first turn sent without the client chip (LAN client, API caller) still runs the starred agent instead of falling through to the router. Star seeds new chats only; an existing chat follows its own history.
- Removing the agent badge (chip ✕) is an explicit "route this myself": the composer sends `sticky_agent: "none"`, which beats both the star and the chat's earlier `/cursor` turns, and the removal is remembered in session prefs (`stickyCleared`) so a refresh does not infer the badge back from history. Tests: `src/tests/chat/test_starred_agent_removal.py`.
- **Task failure is terminal** (the agent ran and reported failure): the reply shows its output plus `/retry` hints; the router never silently reruns it on another model. Only "never ran" failures (quota/usage, auth, missing CLI, connection) walk the fallback chain; a quota error skips that agent's other premium models for the turn (Cursor → `auto` first). Tests: `src/tests/router/test_agent_router.py`.
- **Cancellation is terminal** (`FailureKind.CANCELLED`): a `[CANCELLED]` result from Stop / chat deletion never escalates and never spawns a fallback. Pressing Stop used to buy a Grok escalation plus a Codex run that outlived the turn.
- **Mid-turn steering** (`src/api/agent_harness/steer.py`): Codex turns run on `codex app-server` and Muse Code turns on `muse serve`, so a follow-up sent while they work goes into the live turn (`turn/steer`) through `POST /api/chat-steer` instead of waiting in the follow-up queue. The composer tries steer first and queues on `steered: false` (no steerable run, other slash command, attachments, or turn already finished). The server persists the steered user row (`metadata.steered`). Either runner returns `fallback` if it can't start a turn, and the adapter reruns on `exec`. Off switch: `settings.json` → `agent_steer: {"codex": false}` or env `CUTTLE_AGENT_STEER=0`. Codex reads steer input only at its next sampling step: the status strip shows `steer queued: …`, and a steer the turn never echoed back is listed in the reply (`undelivered_steers`) instead of vanishing. While a Codex turn is live, `/api/agent-context` uses the runner's token snapshot and never resumes the busy thread from a second app-server. Claude Code turns run `claude -p --input-format stream-json --replay-user-messages` with stdin open: a steer is another stdin user line, folded in at the next tool boundary (or answered as an extra turn after the first `result`, merged into one reply); stdin closes at the first root `result` unless the CLI still lists background tasks (`system/background_tasks_changed`) — then the turn stays open and steerable until a later `result` with none pending, so a task's completion notification continues the same reply (cap `CLAUDE_BACKGROUND_WAIT_SEC`, default 3000s, `0` disables). Off switch `agent_steer: {"claude": false}` restores the plain one-shot `-p`. Cursor `-p` has no mid-turn input channel. Tests: `src/tests/harness/test_agent_steer.py`.
- **Turn guard**: `chat_delivery` hands each turn a token (`current_turn` / `is_stale_turn`). A run whose turn was superseded (Stop → re-send) cannot persist its reply, park it for the client, release the live turn's busy lock, or start another routed target.

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

Portable lessons belong in `AGENTS.md`, architecture docs, or tests; public bugs and feature requests are GitHub issues. The maintainer's process backlog is install-local in `.cuttle/personal/learnings/` (`LEARNINGS.md`, `ERRORS.md`, `FEATURE_REQUESTS.md`; gitignored, absent on a fresh clone); use structured entries (`[LRN-YYYYMMDD-XXX]`, `[ERR-...]`, `[FEAT-...]`) with Priority, Status, Area, Metadata. Not Context Compiler `docs/` — open on demand only.

## Important Conventions

- Turn dispatch lives in owned services, not the entry module: `api.agent_harness.runners` (all harness CLI entries), `api.chat_turn` (request/selection seam), `api.chat_coordinator` (shared transport-neutral submit: `PreparedAgentTurn`/`select_agent_turn`/`submit_agent_turn`; route lanes submit claimed, legacy surfaces unclaimed), `api.chat_turn_workflow` (lane orchestration), `api.chat_turn_persist` (saver/user persist). `process_message_with_bot` in `web_chat_api.py` is only the compat entry for `/api/sessions/send`.
- Flask's default HTTPS listener is port **8080** (not 5000); companion/phone ports come from `src/api/server_ports.py`
- Discord token is read from `<home>/.env` — the daemon loads this before child processes; Discord is REST-only, with no inbound bot subprocess
- **Project commands**: `{project}/.cuttle/commands/*.md` (YAML frontmatter + body) appear in the chat `/` palette for that project. See `.cuttle_global/skills/cuttle-project-commands/SKILL.md`. Invoke as `/{name}` or `/cmd {name}` (works after sticky `/cursor` too).

### Chat attachments (images / PDFs)

- No CLI harness (Cursor, Codex, Muse, Hermes) accepts image input. `src/api/vision_prepass.py` turns uploads into a text digest: **Claude vision → OpenAI vision**, and reports the provider error inline rather than degrading into a silent "(no text found)" — otherwise the agent confidently says no image arrived.
- The pre-pass runs in `/api/chat` **above every slash-agent handler** (`_run_attachment_prepass`, just after the starred sticky prefix). It used to run at the bottom, so `/cursor`-badged turns reached the CLI with no description and persisted no attachment metadata.
- The digest is **appended**, never prefixed — each handler matches `^/cursor`/`^/muse`/… against `message_content` and slices the prompt off the front.
- History stores the short `[Attached: …]` note plus `metadata.attachments` (`filename`/`mime`/`url`), not the digest (`_attachment_history_text`). That metadata is what re-renders thumbnails after a refresh; without it the image disappears.
- Uploads land in `<home>/output/uploads/{session}/`, served via `/output/…`. `resolve_upload_refs` rejects any client path outside that root.
- Tests: `src/tests/chat/test_chat_attachments.py`.

## Local file references

Use clickable file links appropriate to the current OS and client. On Windows,
use forward slashes in file/editor URLs; on POSIX use the real checkout path.
Chat-handle formatting belongs to `.cuttle_global/rules/01-chat-handles.md`.
