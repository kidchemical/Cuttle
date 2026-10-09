# Cuttle action forms (global runbook)

Native chat UI for choices, approvals, side effects, and long-job progress.
**Injected agent briefing stays short** — open this file before emitting a
`<cuttle_action_form>`, `<cuttle_confirm>`, or watch card.

Allowlisted recipes live in `{project}/.cuttle/actions/*.yaml` (and global actions
under the Cuttle checkout `.cuttle_global/actions/`). Clicks run via Flask — **no LLM**.
On Linux, shell actions must use `run_posix:` (or a `.py`/`.sh` sibling of a `.ps1`);
PowerShell `run:` lines are Windows-only.

## When to use which

| Situation | Use |
|---|---|
| User must pick A/B/C or answer structured questions | `<cuttle_action_form>` (`choice` / `multi` / `form`) |
| Single yes/no on a final body (Discord post already written) | `<cuttle_confirm action="…">` |
| Side effect (post, restart, allowlisted shell) | Form/confirm option with a **real** action id from the chat project's `.cuttle/actions/` or global `.cuttle_global/actions/` |
| Long OS job (≥ ~1–2 min): build, upload, download | Project command `watch:` **or** watch/progress card (below) |
| Short Q&A / quick edits / seconds-long commands | No form — answer or run inline |

**Do not** ask “A or B?” / “OK to restart?” in bare prose when a form can do it.

## Q&A / preference (no side effect)

Options are **id + label only**. **Omit `action` entirely.**

```xml
<cuttle_action_form>
{
  "mode": "choice",
  "title": "Next step",
  "lock": "form",
  "silent": true,
  "options": [
    {"id": "a", "label": "Option A"},
    {"id": "b", "label": "Option B"}
  ]
}
</cuttle_action_form>
```

- Click → toast `Selected: …` and lock. **No progress bar.**
- By default does **not** auto-resume the agent (user may still reply in chat).
- Set `"resume": true` when the pick should start a turn (e.g. plan Approve /
  CreatePlan bridge). The run endpoint returns `injected_user_message`; the
  client sends it as a normal turn (`sendMessage({text})`), which is the **only**
  writer of the answer bubble — the endpoint does not persist its own copy.
  **Asking the user a question = always `resume: true`**, then end your turn.
- One question, one answer → `"mode": "choice"`.
- One question, several answers → `"mode": "multi"` (checkboxes + **Submit** / **Cancel**).
- **Two or more questions → ONE `"mode": "form"` card**, never several cards.
  Use `radio` (one answer), `checkboxes` (several; field `options` like radio),
  `select`, or `text` fields. Submit sends the agent `[form-answers]` with one
  `- question: answer` line **per field**; unanswered fields come back as
  `(no answer)` / `(none)` so every question is accounted for.
- Cancel never resumes the agent.

**Custom answers are built in.** Every Q&A card that resumes the agent
automatically renders an `Or type your own answer…` input with a Send button,
so the user is never locked into the listed options. Agents must NOT add their
own "Other / custom" option — the renderer owns it. Side-effect and watch cards
never get the custom row. Per-card opt-out: `"allow_custom": false`.

**Multi-question cards answer per question.** A `form` card (2+ questions) does
not get the card-wide row; every `radio` / `checkboxes` / `select` question
carries its own trailing `Other…` option, and picking it reveals that
question's `Or type your own answer…` box. Typed text replaces the answer for
that question only (checkboxes append to the picked list); `plaintext` /
`textarea` / `checkbox` questions are already free-text.

**One Q&A card per reply.** Each `resume` card wakes the agent on its own click,
so a second card in the same reply is orphaned the moment the first is answered.
The server (`rewrite_action_forms` → `merge_qa_resume_specs`) folds 2+ Q&A resume
cards in one reply into a single `form` with one Submit as a safety net, but
emit the combined form yourself. Side-effect cards (real `action`) are never merged.

```xml
<cuttle_action_form>
{
  "mode": "multi",
  "title": "Which areas should I touch?",
  "lock": "form",
  "silent": true,
  "resume": true,
  "options": [
    {"id": "ui", "label": "Frontend"},
    {"id": "api", "label": "Server"}
  ]
}
</cuttle_action_form>
```

Several questions, one card:

```xml
<cuttle_action_form>
{
  "mode": "form",
  "title": "A few questions",
  "lock": "form",
  "silent": true,
  "resume": true,
  "fields": [
    {"id": "scope", "label": "Scope", "type": "radio",
     "options": [{"value": "small", "label": "Small fix"}, {"value": "full", "label": "Full refactor"}]},
    {"id": "areas", "label": "Areas", "type": "checkboxes",
     "options": [{"value": "ui", "label": "Frontend"}, {"value": "api", "label": "Server"}]}
  ]
}
</cuttle_action_form>
```

**Use Cuttle forms for user input, never native harness question/input tools.**
Native picker UIs are not the Cuttle chat UI. Emit the card yourself and end the
turn; unavailable input is neither an answer nor user cancellation.

Harness adapters recover supported native question events into the shared form
contract (`src/api/agent_harness/questions.py`). Server transports close the native
request without supplying an answer and return the card through normal chat
persistence. Submission starts the next turn using the saved native session;
Cuttle does not retain a live input RPC across turns. Invalid payloads produce a
visible recovery error. Vendor event recognition belongs to harness integrations,
not global rules. See `src/api/agent_harness/ADDING_AN_AGENT.md`.

**Never invent** placeholder actions for Q&A. These all fail or used to fail at click time:

| Bad | Why |
|---|---|
| `"action": "none"` | Not an allowlisted recipe → `Unknown action \`none\`` |
| `"action": "noop"` / `"null"` / `"__agent_reply__"` | Same — invented ids |
| Asking “A or B?” in prose | Prefer a form with **no** `action` key |

Server-side, those sentinel strings are now coerced to “no action” (same as omitting the key), but agents must still **omit** `action` for preference picks.

## Side effects (Discord, restart, shell)

Every option (or `submit.action`) needs a **real** allowlisted action name —
e.g. `discord.post`, `flask.restart`, `flask.health`, `git.push`.

**Never invent** actions like `__agent_reply__` or `none`. Unknown ids fail at click time.

```xml
<cuttle_action_form>
{
  "mode": "choice",
  "title": "…",
  "lock": "form",
  "silent": true,
  "options": [
    {"id": "a", "label": "A", "action": "discord.post", "params": {"channel": "feature-updates"}, "body": "markdown body"}
  ]
}
</cuttle_action_form>
```

One-shot confirm:

```xml
<cuttle_confirm action="discord.post" channel="feature-updates">body</cuttle_confirm>
```

Modes: `choice` | `multi` | `form`. Lock: `form` (one-shot) | `field` | `none`.
Also: `<cuttle_button>`, `<cuttle_form>` for lighter chrome. Never invent HTML/files for chat UI.

**Pending rewrite (server):** agents emit a bare `<cuttle_action_form>{JSON}</cuttle_action_form>`.
Flask rewrites to `<cuttle_action_form_pending id="…">` with the **same JSON in the
tag body**. Do **not** put `fallback="inline.<base64 of entire spec>"` on the open
tag — that duplicates the body, bloats the transcript, and is the wrong restart-safe
path. Restart safety = short `id` (in-memory) + body JSON → client `data-spec` rebuild.

## Watch / progress cards (long OS jobs only)

`__watch_resume__`, `__watch_park__`, `__watch_cancel__` are **only** for jobs with a
`watch` block (status JSON + progress). Using them on a Q&A form shows a **progress bar**
and is wrong.

Prefer a project command with `execute: shell` + `watch:` so Flask attaches the card.
If you kick a long job yourself: `python -m api.job_watch write --id <kebab> …`, then:

```xml
<cuttle_action_form>
{
  "title": "Job",
  "mode": "choice",
  "lock": "form",
  "silent": true,
  "watch": {
    "id": "<kebab>",
    "url": "/output/<kebab>-status.json",
    "interval_ms": 4000,
    "done_states": ["done"],
    "fail_states": ["failed"],
    "resume_message": "Job <kebab> finished. Read /output/<kebab>-status.json and summarize. Do not restart it."
  },
  "options": [
    {"id": "continue", "label": "Continue once it's finished", "action": "__watch_resume__"},
    {"id": "later", "label": "I'll reply once it's done", "action": "__watch_park__"},
    {"id": "stop", "label": "Stop job", "action": "__watch_cancel__"}
  ]
}
</cuttle_action_form>
```

Kick the job, emit the card, **stop** — do not poll in a wait loop.
Stop kills the OS process tree in the status JSON (`pid`, `worker_pid`, `pids`; legacy `unity_pid`).

### Multi-bar progress (required for mesh / multi-worker jobs)

When a watch card tracks **mesh workers** (blender shards, farm bake, 2+ devices),
status JSON **must** include a `bars` array: overall primary + one worker bar each.
Single-machine jobs may keep a lone top-level `percent`.

```json
{
  "state": "running",
  "percent": 72,
  "label": "Mesh bake — 188/263 frames",
  "bars": [
    {"id": "overall", "label": "Overall", "percent": 72, "kind": "primary", "detail": "188/263"},
    {"id": "worker-a", "label": "worker-a", "percent": 80, "kind": "worker", "detail": "10/12 chunks"},
    {"id": "worker-b", "label": "worker-b", "percent": 64, "kind": "worker", "detail": "8/12 chunks"}
  ]
}
```

- `percent` (top-level) stays the overall value for older clients.
- `kind`: `primary` (thick accent bar) vs `worker` / `secondary` (thinner indented bars).
- Mesh helper: `python -m api.device_workers.cli batch-watch --batch-id <id> --id <watch-id>`
  (writes overall + per-worker `bars` from `batch-status`).
- Manual: `python -m api.job_watch write … --bars-json "[…]"` or `write_status(..., bars=[...])`.

## Flask restart (Cuttle chat)

Authenticated HTTP restart-card submissions call `api.flask_restart.request_restart`
directly after token/session validation and owner authorization. `flask.restart`
is a native recovery control on this path: project recipe discovery, personal
configuration and shell execution cannot override or block it. The legacy shell
recipe remains for standalone callers. Native `/restart` and the owner-only
restart endpoint are independent recovery paths.


Never kill Flask, the daemon, or the Discord bot (`kill`/`pkill`, `taskkill`/`Stop-Process`) from an agent
Flask is hosting. After Python changes need a restart, emit:

```xml
<cuttle_action_form>
{
  "mode": "choice",
  "title": "Restart Flask (daemon-owned)",
  "lock": "form",
  "silent": true,
  "options": [
    {"id": "status", "label": "Status — show restart state + active work", "action": "flask.restart", "params": {"mode": "status"}},
    {"id": "graceful", "label": "Graceful — restart now (rejects if busy)", "action": "flask.restart", "params": {"mode": "graceful"}},
    {"id": "when-idle", "label": "When idle — wait for active jobs to finish", "action": "flask.restart", "params": {"mode": "when-idle"}},
    {"id": "force", "label": "Force — interrupt active work", "action": "flask.restart", "params": {"mode": "force"}},
    {"id": "health", "label": "Health check only (no restart)", "action": "flask.health", "params": {}}
  ]
}
</cuttle_action_form>
```

**Linked across chats:** do **not** invent a custom form `id`. The server stamps every
Flask-restart controller with a shared id `flask-restart-g{generation}` (daemon
Flask generation). Cards in every open chat stay linked until Flask actually
restarts; then the generation bumps and the next batch of cards gets a new id.
Clicking Graceful/When-idle/Force on one card settles the others.

**Do not soft-dismiss restart cards:** sending a follow-up chat message collapses
ordinary one-shot forms as “Ignored”. Flask restart controllers are exempt —
otherwise the shared `flask-restart-gN` id gets poisoned and a later click returns
`already_locked` with toast **Ignored**. Soft “Ignored” locks on those ids are
also healed on render / click.

Surfaces without action buttons (Discord, plain API, terminal/CLI session): never emit raw `<cuttle_action_form>` markup — cards only render in Cuttle chat, elsewhere it prints as dead text. Use `/restart graceful`,
`/restart when-idle`, `/restart force --yes` (or `POST /api/flask/restart`).

**The launcher/child trap:** the daemon tracks the venv Python **launcher**;
on some installs (e.g. Windows venvs) a separate base-interpreter **child** holds
port 8080. Killing only the launcher orphans the child → restart loop. Never kill
half the tree — use the coordinated daemon restart, and leave unrelated processes
(local model servers, guest MCP servers) alone.

**Verify (HTTPS + self-signed on 8080):**

```bash
curl -k -s -o /dev/null -w "%{http_code}" https://127.0.0.1:8080/api/health   # expect 200
curl -k -s https://127.0.0.1:8080/api/flask/restart/status
```

On Windows PowerShell use `curl.exe` (not the `curl` alias) and `-o NUL`.

Do not report "restart complete" until `/api/health` (or restart status
`healthy`) succeeds.

## Related

- Layout / authoring: [commands-and-actions.md](commands-and-actions.md)
- Discord read/post: [discord.md](discord.md)
- Skill (deeper examples): `.cuttle_global/skills/cuttle-project-commands/SKILL.md`

### Standard restart controller

Standalone restart choosers are normalized by `api.flask_restart` to one title,
button order, labels, and explicit modes. Agent-authored wording does not select
behavior. Existing option ids are preserved for transcript compatibility; mixed
forms containing other actions are not replaced. All restart controllers retain
the shared generation identity. Emit the full standard template above, without shortening or relabeling its options; the live “waiting for tasks” indication begins after When idle is selected and uses the submitted restart’s actual state.

Authenticated restart submissions carry the actual request's restart id/state
back to the card directly. Progress must not depend on rereading a global status
file. A persisted “Flask restart acknowledged” message is an acceptance receipt,
not a completion lock; it must not be replayed as a green finished result on a
later card in the same generation.

## Action replay policy

Resolved allowlisted YAML recipes own replay policy. Mutating shell and service
recipes default to `once`; read-only recipes can declare `replay: reusable`.
A mixed recipe may use:

```yaml
replay:
  default: once
  modes:
    status: reusable
```

A card's `reusable`/`lock` affects presentation; it cannot override recipe policy.
One-shot dispatch claims are atomic and durable in the auth database. Pending
confirms and their signed fallback tokens share a receipt, so restarting Flask
or concurrent clicks cannot dispatch the same confirmation twice. Form receipts
also survive regeneration of synthetic tokens. Native restart controls claim
mutating selections; Status remains reusable and an explicit pre-dispatch
rejection permits another attempt.

A receipt guarantees at most one dispatch, not exactly-once external effects.
Ambiguous failures keep the receipt; request a fresh card/token to retry. Cancel
claims the same receipt as Confirm. Fresh confirmations get distinct identities.
Legacy signed tokens without a receipt use their token digest. Form receipts
persist; expiring inline-token receipts can be reclaimed after token expiry.
