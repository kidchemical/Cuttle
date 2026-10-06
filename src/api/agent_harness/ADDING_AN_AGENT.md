# Adding an agent to the harness

Folder-per-agent. Adding an agent means filling `agents/<id>/` — **not** forking `web_chat_api.py`.

```
agents/<id>/
  manifest.yaml   # declarative record (id, label, slash, models, resume, hints)
  adapter.py      # build_adapter() -> object with available()/resolve_cwd()/*_resume()/execute()
  __init__.py     # optional for bundled package agents
```

The shared kernel (`kernel.py`) handles status, query tracking, resume, **Context Compiler**
injection, hot-swap handoff, and error shaping for every agent. The adapter is a thin CLI
wrapper returning `AgentResult`.

Shared project context (rules, inventory, handoff) comes from **Cuttle Brain** —
`src/api/cuttle_brain/` — not from copying each vendor’s skill registry. See
[file:////path/to/Cuttle/src/api/cuttle_brain/CONTEXT_COMPILER.md](file:////path/to/Cuttle/src/api/cuttle_brain/CONTEXT_COMPILER.md).

## Where folders live (discovery)

| Root | Purpose |
|---|---|
| `src/api/agent_harness/agents/<id>/` | **Bundled** first-party connectors (shipped with Cuttle) |
| `CUTTLE_AGENTS_DIR` (pathsep list) + `.cuttle_global/personal/agents/<id>/` | **User / instance** drop-ins |
| `{Cuttle}/.cuttle_global/agents/<id>/` | Shared instance drop-ins (same contract) |
| `{project}/.cuttle/agents/<id>/` | **Project** drop-ins (chat project path) |

Legacy `src/data/harness_agents/` packs remain discoverable until the offline
runtime migration moves them into the personal instance root.

Bundled ids always win — a drop-in cannot shadow `cursor` / `codex` / `muse` /
`claude` / `opencode` / `antigravity` / `hermes` / `deepseek`. Project drop-ins
may override user drop-ins of the same id. Palette + `/api/agents` surface `available`,
`status` (`ready` \| `missing_cli`), and `install_hint`.

### Project drop-in trust model (P6-B)

Project drop-ins execute third-party `adapter.py` code **only** on explicit
opt-in: `CUTTLE_ALLOW_PROJECT_ADAPTERS=1` (env) or
`settings → agent_harness.allow_project_adapters` (Settings → Agents →
"Run project adapters"). Without opt-in the folder is
never imported and never listed — opening an untrusted project cannot run its
adapters. Opting in means: **that project's `.cuttle/agents/` content is
trusted code**, same as any installed CLI plugin. There is no Python-level
sandbox; containment is the opt-in gate plus the rules below.

- **Validate before import.** Folder name is the canonical id
  (lowercased, `_` → `-`, must match `[a-z0-9]+(-[a-z0-9]+)*`); `slash` is
  canonicalized the same way and must match `/[a-z0-9]+(-[a-z0-9]+)*`.
  Anything else (e.g. `slash: "/"`, `Evil Agent/`) is skipped **without
  importing** `adapter.py`. Unknown `capabilities_inject` / `env_profile` /
  `activity` values fall back to defaults instead of failing.
- **Load-once (mtime-keyed).** Each external `adapter.py` executes once per
  process; editing `adapter.py` or a top-level sibling `.py` reloads it on
  the next discovery (no restart). Cache cleared by `reload_catalog()`.
  Top-level code must still be idempotent.
- **Sibling imports: relative only.** Each drop-in loads as its own
  namespace package with **no `sys.path` mutation**, so
  `from . import helper` / `from .sub.deep import VAL` resolve on demand
  through the package, isolated per drop-in (dotted subpackages included).
  Files the adapter never imports are never executed, and a broken file the
  adapter never imports cannot break the adapter. Relative imports keep
  working after load (the package persists), including inside adapter
  methods. Migration from a flat layout is one word: `import helper` →
  `from . import helper`.
- **Purge lifetime.** `reload_catalog()` (tests / hot-add only — no
  production caller purges mid-turn) drops the namespaced package: a
  previously returned instance keeps its already-bound top-level references,
  but a NEW lazy relative import afterwards raises `ImportError` until
  fresh discovery returns a new adapter (pinned in
  `tests/test_harness_project_adapters.py`).
- **Bare absolute imports are not sibling imports.** `import helper` inside
  a drop-in resolves against the ambient environment (stdlib /
  site-packages / live modules) and fails loudly otherwise — the adapter is
  skipped with the missing-module error. Nothing is aliased, scanned, or
  pre-loaded to satisfy it, and a drop-in file can never shadow stdlib.
- **Concurrency scope.** Drop-in loads are serialized against each other
  (one exec per adapter even under threaded discovery); this makes no claim
  about unrelated Python imports on other threads.
- Same-named siblings in two drop-ins never leak into each other
  (see `tests/test_harness_project_adapters.py`).

## Hard-won gotchas (bake these in — do not rediscover)

Every item below already burned dogfood time. The ADD process and smoke suite exist to
catch them *before* the user does.

### 0. Project chip is cwd (CH-000164)

The chat project chip is the working directory for **every** agent. The kernel
(`resolve_harness_cwd`) maps that path to a folder (including Unity `source/` /
Cuttle `src/` nested git) and **rejects** an adapter that returns a different
project.

Do **not**:

- fall back to `os.getcwd()` (Flask's cwd is Cuttle) when the chip path exists
- pin `--workspace` / CLI cwd to whichever folder last saved a `--resume` UUID
  for this chat — that kept Escape Purgatory chips running inside Cuttle
- reimplement `abspath` + `isdir` + `getcwd` in a new adapter

Call `api.agent_harness.cwd.resolve_harness_cwd`. Same-repo aliases (`src/` /
`source/`) may share a resume; a different registered project must start a
**new** CLI session. Switching chips is supposed to change the repo.

`src/tests/test_agent_harness.py` (`test_every_bundled_adapter_honors_project_chip`,
`test_kernel_rejects_adapter_cwd_from_another_project`) covers current **and**
future bundled agents.

### 1. Windows `.cmd` / `.bat` shims vs native `.exe` (ERR-20260816-007 + Cursor `%*`)

`shutil.which("tool")` on Windows often returns `tool.CMD` (npm / installer shim). That
shim re-parses argv through `cmd.exe` (`%*`), which:

- truncates at the **first newline**,
- hits the ~8,191-character CreateProcess limit earlier,
- adds an extra process and quoting hazards.

**Required pattern:** discover via PATH/shim, then prefer a packaged native binary.
Shared helper: `api.agent_harness.win_cli.which_preferring_native` /
`prefer_native_binary`. OpenCode already does this; Cursor uses `node.exe`+`index.js`
for the same reason. **Never** put long multiline prompts on argv when the CLI accepts
stdin / a response file / a native system channel.

### 2. Silent prompt truncation is never OK

Do not “protect” Windows by slicing the user prompt (the OpenCode 2,800-char mistake).
Cuttle prepends the Context Compiler envelope on first turns — a short argv cap drops the
*user’s* request first. Transport must be lossless (stdin / file / native channel). Offline
smoke must send a long pad + **end sentinel** and assert the adapter did not put the
sentinel on argv.

### 3. Resume stores must coerce session ids (ERR-20260816-001, ERR-20260816-009)

DB `chat_session_id` is an **int**. The kernel normalizes to `str` once
(`normalize_chat_session_id`), but stores must still `str(...)` defensively. Copying a
store that calls `.strip()` on the raw value recreates
`'int' object has no attribute 'strip'`.

Fixing one store is not enough: the same bug shipped in `cursor_cli_session_store.save_…`
and ran for a full day of `/cursor` turns, each one a brand-new memory-less CLI session
while badges and run history still looked healthy. Two rules came out of it:

- **Save is a direction too.** Testing `load`/`clear` only is how it survived.
- **Never fix one store alone.** `src/tests/test_agent_resume_contract.py` *discovers*
  every `*session_store*.py` under `src/` and fans the contract over it, so a new store
  is covered the moment it exists — including pre-harness paths (cursor, codex, muse).
  If your store hides its map file behind something other than `_map_file()`, the
  contract test fails on purpose: expose it so tests can isolate state.

### 4. Auth ≠ install; never leak raw stderr (ERR-2/3/6)

CLI present ≠ authenticated. Shape failures to **one actionable line**; emit
`<cuttle_action_form>` for allowlisted key sync (never ask the LLM to paste secrets).
Provider keys are not interchangeable — see Auth sync below.

### 5. Hot-swap: resume per agent + Cuttle transcript handoff

Cuttle owns the neutral chat transcript (SQLite). Each agent keeps its **own** resume id
for the chat.

| Event | Behavior |
|---|---|
| Same agent again | Use that agent’s native resume; **no** handoff delta (preserve native memory). |
| Sticky agent changes | Still use the *target* agent’s resume if present; inject a short **handoff delta** from recent Cuttle turns. |

Do not clear other agents’ resume maps on switch. Kernel records last agent via
`api.cuttle_brain.handoff.record_last_agent`.

### 5b. Live status must match Cursor (thinking / tool N / writing)

Chat status is a **strip**, not a bubble dump. Cursor’s methodology is the house style
for every harness that can stream:

| Line | When |
|---|---|
| `thinking: {preview}…` | Reasoning / thought deltas (throttled) |
| `tool {N}: {summary}` | Each tool / command / MCP / file edit start |
| `writing: {preview}` | Assistant text deltas (throttled; leading `…` only when shortened) |
| `{Agent} working… {Ns} ({last_activity})` | **Only** after ~15s of silence |

Shared helper: `api.agent_harness.activity.ActivityEmitter`. Map vendor NDJSON in the
CLI drain; **do not** also run `heartbeat_status("X working", interval=4)` in the
adapter — that stomps live tool lines (Codex dogfood: UI stuck on
`Codex working… 360s` while JSONL was emitting commands). Agents with no mid-run
stream may keep a coarse 15s heartbeat only, after checking the vendor's
documented headless output modes. Claude, DeepSeek Harness, and Antigravity
provide structured event streams and must use them. Hermes quiet mode uses
invocation-scoped transcript polling, with a heartbeat until it exposes a
session id; never attach progress to an arbitrary newest active session.

The inspector's text is separate from these compact status lines. Streaming
adapters use `TextActivityLog(agent_id)` from the same shared module: `start`
opens an item, `delta` appends actual chunks, and `save` records cumulative
snapshots. Keep vendor item ids when available. Save completed text regardless
of the status throttle and `flush` in cleanup to preserve interrupted blocks.
Use `ActivityEmitter(..., record_text_previews=False)` (or `put_status` with
`preview_only=True`) so `QueryStatusTee` forwards previews without replacing
full inspector text. `text_preview` supplies the compact writing label.
Text retains the query-log `MAX_TEXT` cap, with an explicit truncation marker.
`ToolActivityLog` records bounded arguments/results and completion/failure by
vendor tool id; use `record_tool_previews=False` when it owns the structured
tool events. Progress must not replace the authoritative terminal result,
session id, usage, or question forms. Child result events must not replace
their parent's answer. Tests must cover fast blocks, interruptions, and tool
failures with fake CLIs before marking a streaming adapter ready.

### 6. Context Compiler is shared — adapters only transport

Adapters do **not** invent their own system-prompt piles. The kernel calls
`compile_context` (core contract + `.cuttle/rules` + profile + inventory + optional
handoff + user request). Adapters declare transport (prefix/stdin today). Optional debug
CLI: `python -m api.cuttle_brain compile --project … --prompt …`.

**Mid-turn steering (optional).** If the vendor CLI has a server mode that accepts
input into a running turn (Codex `app-server`, Muse `serve`), register a send
callback with `api.agent_harness.steer.register(chat_session_id, agent_id, send)`
once the turn id is known, and `unregister` when it ends. `send(text)` returns a
Future of `(ok, error)`. Add the id to `STEERABLE_AGENTS`, and fall back to the
one-shot path when the server cannot start a turn. Reference:
`scripts/utilities/codex_app_server_turn.py`.

### 7. Automation is Cuttle-owned (not adapter-owned)

Builds, deploys, downloads, and other long OS jobs must **not** be reimplemented in
each CLI adapter. Do not teach Cursor one `/build` dance and Codex another.

| Piece | Owner |
|---|---|
| `{project}/.cuttle/commands/*.md` with `execute: shell` + `run:` + optional `watch:` | Cuttle (Flask expands `/name` **before** any agent) |
| Status JSON `src/output/<id>-status.json` (`python -m api.job_watch`) | Cuttle |
| Watch card (progress bar, Continue / I'll reply) | Cuttle chat UI |
| Adapter `execute()` | Only the vendor CLI for *chat* turns |

`/cursor /build` (or `/codex /build`, …) still hits the **shell** path when the command
declares `execute: shell`. The sticky agent is not the orchestrator. Continue on the
watch card starts a **new** chat turn (whatever agent is sticky) to summarize or explain
failure — that is the LLM's remaining job.

New agents inherit this automatically. Do not add per-adapter “how to build Unity”
prompts. If a job has no project command yet, the shared UI contract (item 7 in
`cuttle_ui_capabilities`) still tells every agent to kick + watch form — still no
adapter code.

## CLI installation (BYO-CLI)

Cuttle does not install vendor CLIs — no npm installs, no remote-script
download/execute, no update logic (Phase 6 P6-A retired the
`agent_harness.installer` execution machinery, including unpinned
remote-script auto-install). Bundled connectors declare **guidance
only** so a missing CLI answers with an actionable message:

```yaml
executable_names: [tool-name]
missing_cli_hint: >-
  Install Tool and ensure `tool` is on PATH (see https://…/docs/).
install_hint: >-
  Install `tool` yourself (`npm i -g tool-package`), then authenticate
  with the CLI's native login/configuration (for example `tool auth login`).
```

### Declare native authentication

Guest CLIs own authentication. Set `auth_command: tool auth login` when the CLI
has a login command, and describe its native configuration in `install_hint`.
Do not declare `credential_env` to request provider keys in Cuttle Settings.
CLI presence is not proof of a valid login; report authentication failures from
the CLI and direct the user to its own login/configuration.

## Frozen adapter contract

Keep this surface stable. New agents = new folders, not new kernel APIs.

- `available() -> bool`
- `resolve_cwd(project_path) -> str` — call `resolve_harness_cwd`; never snap to process cwd
- `load_resume` / `save_resume` / `clear_resume(cwd, chat_session_id: Optional[str])`
- `execute(...) -> AgentResult`

`chat_session_id` is an **opaque string** at the adapter boundary. The kernel coerces the
DB int (and `db_session_*` aliases) once via `normalize_chat_session_id`. Still coerce with
`str(...)` in stores defensively — copying an old pattern without that is how
`'int' object has no attribute 'strip'` keeps recurring (ERR-20260816-001).

On Windows npm CLIs, resolve executables with `win_cli.which_preferring_native` (or equivalent)
and transport long prompts losslessly.

Optional `schema_version` in the manifest (default `1`) is reserved for rare protocol bumps.

## The rule: test it before the user does

> An "add agent" task is **not done** until it has passed a contract test *and* a live smoke run
> **on the model and scope the person adding the agent chose**. Do not hand a new agent to the
> user as the first tester. Every issue the user hits that a 30-second smoke test would have
> caught is a process failure — log it in `.cuttle/learnings/ERRORS.md`.

This exists because the Gemini pilot shipped without a test: the user hit
`'int' object has no attribute 'strip'` (ERR-20260816-001) on turn one, then a raw wall of CLI
auth stack traces (ERR-2/3). Both were catchable before handoff.

## Ask before you spend (required)

Live smokes are **real CLI turns** (Cursor Auto can be 2–4+ minutes each). They are not
thousands of prompts, but fanning the default suite across every installed agent on a premium
id will cook the budget. **Ask the person adding the agent before you run `-k live`:**

1. **Which model?** Prefer the cheap sibling (`manifest.smoke_model`). Examples: Muse
   **Contributor** (`muse-spark-1.2-contributor`), DeepSeek **Flash** (`deepseek-v4-flash`).
   Do not default to Pro / non-contributor / Auto-premium just because that is the daily
   `default_model`.
2. **Which agents?** Almost always **the new id only**, not the whole catalog.
3. **Which scope?** For a first add, `min` = one short one-shot (`Reply with exactly the word: pong`).
   Do **not** run `long_pad` (a 2,800+ character integrity prompt) or resume unless asked.
   Envelope + resume are extra turns; they are opt-in when adding.

Set these **before** pytest:

```
set CUTTLE_AGENT_SMOKE=1
set CUTTLE_AGENT_SMOKE_AGENTS=deepseek
set CUTTLE_AGENT_SMOKE_SCOPE=min
set CUTTLE_AGENT_SMOKE_MODEL=deepseek-v4-flash
```

| Env | Meaning |
|---|---|
| `CUTTLE_AGENT_SMOKE=1` | Enable live tests at all |
| `CUTTLE_AGENT_SMOKE_AGENTS` | Comma ids (default: **entire catalog** — do not leave this unset when adding) |
| `CUTTLE_AGENT_SMOKE_SCOPE` | `min` / `one_shot` / `envelope` / `resume` / `long_pad` / `full` |
| `CUTTLE_AGENT_SMOKE_MODEL` | Override every agent in this run |
| `CUTTLE_AGENT_SMOKE_MODEL_<ID>` | Per-agent override (`MUSE`, `DEEPSEEK`, …) |

Model resolution: per-agent env → global env → `manifest.smoke_model` → `manifest.default_model`.
Put the cheap id in `smoke_model` so a forgotten env still does not spend Pro.

## Required steps (in order)

1. **Write the folder** — `manifest.yaml` + `adapter.py`. Keep CLI-specific quirks in the adapter.
   Put first-party agents under the bundled root; put experiments / third-party packs under
   `.cuttle_global/personal/agents/` or `{project}/.cuttle/agents/`.
   For a bundled agent, add a declarative installer when an official source exists.
2. **Inspect the real CLI.** Install it, run `--version`, `--help`, and any `help` subcommand,
   then map its non-interactive prompt, structured output, resume, model, timeout, permission,
   and **auth** flags from actual output. Do not infer flags from another agent.
   On Windows, check whether PATH resolves to `.cmd` and locate the packaged `.exe`.
3. **Auth sync (if cloud keys are required).** Add or reuse an allowlisted sync action; document
   which Cuttle env vars map to which CLI providers; ensure fresh-install / unauthorized replies
   emit a `<cuttle_action_form>` (see **Auth sync** above). Skip only if the CLI is fully covered
   by shared process env with no separate store (still document the env vars).
4. **Contract test (offline, always runs in CI).** Extend `src/tests/test_agent_harness.py`:
   - discovery (`list_agents()` includes your id),
   - slash + sticky prefix shape,
   - `match_slash_command('/<id> hi')`,
   - `public_catalog()` row has `available: bool`, `status`, `install_hint`,
   - `public_catalog()` row has `ready: bool`, `credential_env`,
     `credential_present`, `auth_command` — and declares an auth story at all
     (`auth_command` or native configuration guidance; see
     **Declare your auth story**),
   - dispatch runner registered,
   - **project chip cwd**: `resolve_cwd(tmp_project)` stays in that folder even if
     process cwd is somewhere else; kernel must not honor an adapter that returns
     a different project (see `test_every_bundled_adapter_honors_project_chip`),
   - **resume store round-trips a raw int `chat_session_id`** (stores must still coerce),
   - kernel passes a **str** session id into resume/execute,
   - missing-CLI path returns `available() is False` cleanly (no exception),
   - Windows binary helper prefers `.exe` over `.cmd` when both exist (if applicable).
5. **Error-shaping + prompt-integrity test.** Feed a realistic failure (auth wall, quota, missing
   binary) through the adapter/tool and assert the surfaced error is **one actionable line**.
   Auth failures must mention the sync action or `cli auth login`. See
   adapter stderr summarizers + `src/tests/test_agent_harness_smoke.py`.
   If you added a sync script, cover it with a small unit test that asserts keys never appear in
   stdout (pattern: `src/tests/test_agent_cli_env.py`).
   Also assert **prompt integrity** with a payload longer than any argv threshold and a
   sentinel at the end. Silent truncation is never an acceptable workaround.
6. **Resume + handoff tests (required).**
   - Offline: save resume id → second `execute` receives the same `--session` / resume arg;
     kernel records last agent; switching agents injects a handoff delta while still loading
     the *target* agent’s resume when present.
   - Offline, **argv level**: run two turns through the real dispatch with a fake CLI and
     assert turn 2's argv carries the resume flag. A store round trip alone passes while the
     command line silently omits `--resume` (ERR-20260816-009) — see
     `test_cursor_run_saves_resume_then_passes_it_on_the_next_turn`.
   - Live (gated): two-turn smoke — turn 1 stores a nonce, turn 2 with the same
     `chat_session_id` asks for it and must get it back (proves native resume works).
7. **Run the offline suite:**
   ```
   .venv\Scripts\python.exe -m pytest src/tests/test_agent_harness.py src/tests/test_agent_harness_smoke.py src/tests/test_agent_resume_contract.py src/tests/test_context_compiler.py src/tests/test_agent_cli_env.py -q
   ```
8. **Live smoke (gated) — ask first, then spend.** Never run the unscoped default when adding
   an agent (that fans one_shot + envelope + resume across *every* installed CLI). After the
   person adding the agent picks model / ids / scope:

   ```
   set CUTTLE_AGENT_SMOKE=1
   set CUTTLE_AGENT_SMOKE_AGENTS=<new-id>
   set CUTTLE_AGENT_SMOKE_SCOPE=min
   set CUTTLE_AGENT_SMOKE_MODEL=<cheap-id>
   .venv\Scripts\python.exe -m pytest src/tests/test_agent_harness_smoke.py -q -k live
   ```

   What each scope does (one prompt per selected agent, not thousands):

   | Scope | What it runs |
   |---|---|
   | `min` / `one_shot` | One short “reply pong” turn |
   | `envelope` | Fresh session + short ask; fails CH-000150-8 envelope narration |
   | `resume` | Two-turn nonce recall (skipped if `resume: false`) |
   | `long_pad` | One >2,800-character prompt with an end sentinel (**opt-in**) |
   | `full` | All of the above |

   If auth/quota/CLI is broken, this fails here — the whole point — so you fix or report it
   instead of the user finding out. If smoke fails on missing auth and Cuttle already has the
   key, run the sync action first, then re-smoke.
9. **Only then** enable it for the user. If a live issue is external (account/quota), say so plainly
   and log it in `.cuttle/learnings/ERRORS.md`; don't present a broken agent as ready.

## Bundled connectors (shipped)

| id | CLI | Notes |
|---|---|---|
| `cursor` | `agent -p` | stream-json, multi-segment continue; meta `/model` `/plan` in adapter |
| `codex` | `codex exec` | resume via thread id; optional `reasoning_effort`; Cursor-style JSONL activity (`tool N:` / `thinking:` / `writing:`) |
| `muse` | `muse exec` (WSL) | `--prompt-file` for long prompts; `/muse model` in adapter |
| `claude` | `claude -p` | per-chat `--resume`; `/claude model [refresh]` + `/claude effort` pins → `--model` / `--effort`; catalog from SDK `initialize` (no turn) |
| `opencode` | `opencode run` | native CLI authentication |
| `antigravity` | `agy` | auto-install |
| `hermes` | `hermes chat -Q -q` | local or OpenRouter via config.yaml; `requires_cloud: false`; per-chat `--resume` + state.db status poll |
| `deepseek` | `dsh --profile headless` | Flash by default (`smoke_model` = Flash); no native per-chat resume; needs `DEEPSEEK_API_KEY`. Tentacle for DeepSeek Harness — steal kernel ideas separately ([`MODULARITY.md`](../../../docs/guides/MODULARITY.md)); do not treat dsh as Cuttle’s core. |

## After code changes

Adapters and the kernel are imported by Flask (production mode, no reloader), so Python changes go
live only after a **daemon-owned** restart (`/restart graceful` or the `flask.restart` action card).
Never `taskkill` `web_chat_api` from an agent hosted by that Flask.


## Native questions and user input

Global rules describe Cuttle forms, never vendor tool names. Native tool/event
recognition belongs to each adapter (or its CLI transport helper). Use
`api.agent_harness.questions.QuestionBridge` to normalize captured arguments,
deduplicate repeated events and render one resuming Q&A card. Never fabricate an
answer, cancellation or approval when the native UI is unavailable. Malformed
recognized payloads must yield a visible recovery error.

Server transports stop their owned connection on a recovered question, preserving
the saved session id. The native RPC receives a deferral error, not an answer.
Normal chat persistence/rewrite delivers the card; the existing form submission
sends the answer as a new user turn and resumes that session. No vendor RPC or
process must remain waiting for a picker that Cuttle cannot display. User Stop
still takes precedence and must never become a question or fallback run.

Supported recovery paths: Cursor stream-json AskQuestion; Codex app-server
requestUserInput and exported input-tool items; Muse serve input-tool items and
input RPCs, plus exported exec tool/lifecycle payloads; OpenCode exported question
parts; Claude print mode disables AskUserQuestion and recovers attempted calls
when permission_denials includes their arguments. CLI versions that do not export
question arguments cannot be reconstructed: use the form instruction and the
vendor's native-tool disabling mechanism where available. Test vendor recognition
with fake processes/event fixtures, never paid CLI calls in ordinary tests.

### Guest process environment

Use `core.agent_cli_env.agent_cli_env()` for every CLI subprocess, including
model discovery, usage probes, and persistent servers. Cuttle provider credentials
must never override guest CLI authentication. Preserve native configuration paths
and let the CLI read its own login/configuration. Do not load Cuttle `src/.env`
in adapters or embed its credentials in argv. Catalog `credential_env` must not
request Cuttle API keys for guest CLIs.
