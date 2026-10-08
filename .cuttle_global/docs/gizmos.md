# Gizmos (experimental)

**Widgets** render inside an agent's chat bubble (charts, forms). **Gizmos**
live outside the transcript, in the shell: docked on the Electron title bar or
the blade bar, floating over every Space, or popped out as an always-on-top
desktop window. They persist install-wide and stay put across chats.

Usage meters and install-wide docks are gated by the `gizmos` experimental
flag (default off). Tasks are established chat functionality and remain
available through their scoped Gizmos API/CLI when this flag is off.

## Feature availability and agent guidance

Tasks instructions apply independently of the `gizmos` flag. Global rule text
is not automatically filtered by experimental flags; an operation pointer is
not evidence that its feature is enabled. Before using optional gizmos, inspect
the resolved flags with `python -m api.experimental list`. Respect disabled
features and the process kill switch; do not enable an experiment just to
follow its runbook without the user's instruction to enable it.

For future gizmo types, document whether each type is generally available or
which registry flag owns it. Experimental operational guidance should belong
to an enablement-scoped feature bundle, including its rules, docs and skills.
The current context loaders do not yet support these bundles; conditional
wording above documents current operation, not the intended final architecture.
Enforce the resolved gate in the feature's service/CLI/HTTP and UI paths;
instructions alone cannot enforce availability. Shared Gizmos infrastructure
does not make every type experimental or every type enabled.

The first type is **`usage_meter`**: plan budget left for one agent (Codex,
Claude Code, Cursor) plus when the tightest window resets, or when a
blocked account unblocks. It is the same data as `/usage`, normalized.

## Placement

| Dock | Where | Fallback |
|---|---|---|
| `titlebar` | Electron title bar, right side | blade bar in a browser |
| `rail` | leftmost blade bar, above the footer | — |
| `float` | over the shell, every Space; `x`/`y` are 0..1 viewport fractions | — |
| `popout` | always-on-top desktop window (one per gizmo) | `float` in a browser |

`order` sorts gizmos within `titlebar`/`rail`. Users drag gizmos between
docks, or click/right-click one for details, agent, move, refresh, notify,
remove. Apps → **Gizmos** creates and edits them without an agent.

## Agent ops CLI

```bash
PYTHONPATH=src .venv/bin/python -m api.gizmos types
PYTHONPATH=src .venv/bin/python -m api.gizmos list
PYTHONPATH=src .venv/bin/python -m api.gizmos create usage_meter --agent claude --dock titlebar
PYTHONPATH=src .venv/bin/python -m api.gizmos create usage_meter --agent codex --id codex-meter --dock float --x 0.8 --y 0.05
PYTHONPATH=src .venv/bin/python -m api.gizmos update codex-meter --show used --window weekly --title "Codex weekly"
PYTHONPATH=src .venv/bin/python -m api.gizmos move codex-meter --dock popout
PYTHONPATH=src .venv/bin/python -m api.gizmos data codex-meter --refresh
PYTHONPATH=src .venv/bin/python -m api.gizmos usage codex
PYTHONPATH=src .venv/bin/python -m api.gizmos remove codex-meter
```

JSON out; exit `0` ok, `1` flag off, `2` invalid/not found. Writes bump a
store revision the shell polls every few seconds, so open windows update with
no restart and no chat message. Check `list` before creating a meter; do not
create a second meter for an agent that already has one unless asked.

`usage_meter` config: `agent` (`codex` | `claude` | `cursor`), `window`
(`tightest` or a window id from `usage <agent>`, e.g. `five_hour`, `weekly`),
`show` (`remaining` | `used`), `notify_on_unblock` (bool, default off).
Changing the agent keeps a default title in step; a custom title sticks.

### Notify when unblocked

The click panel has a **Notify when unblocked** button. Arming it queues a
tray + UI-toast notification the next time the shell sees that account go
from blocked to open, then disarms (one-shot). Agents use the same flag:

```bash
PYTHONPATH=src .venv/bin/python -m api.gizmos update codex-meter --notify-on-unblock
PYTHONPATH=src .venv/bin/python -m api.gizmos update codex-meter --no-notify-on-unblock
```

The shell must be open to observe the transition (same caveat as
completion notifications); the toast drains into every open Cuttle window,
phone browser included. Raw verbs: `python -m api.ui_notify send "…"`.
The panel is clamped inside the viewport (20 px bottom clearance for the
phone gesture bar) and scrolls when taller than the screen.

## REST (owner session)

- `GET /api/gizmos` → `{revision, gizmos, types}`
- `POST /api/gizmos` `{type, config, placement, title?, id?}`
- `GET|PATCH|DELETE /api/gizmos/<id>` (`PATCH` takes partial `title`/`config`/`placement`)
- `GET /api/gizmos/<id>/data[?refresh=1]` — live data (usage cached 60 s; forced refresh at most every 15 s)

## Extending

- New usage agent: `api.gizmos.usage.register_provider(agent, label, fetch)`
  where `fetch()` returns `{windows, blocked, plan, extras, error}` (see the
  module docstring). It shows up in every agent picker automatically.
- New gizmo type: `register_type(GizmoType(...))` in `api/gizmos/catalog.py`
  plus a renderer branch in `src/web/js/gizmos/gizmos_model.js`.

## Tasks gizmos (composer dock)

For multi-step work, **create a Tasks gizmo at the beginning of planning**, then
patch the same id as work progresses. These are tool calls: do not emit
`<cuttle_widget>` Tasks tags or a second list in a final reply. First inspect
active lists from the context digest or `tasks list` and reuse the list for the
same concern. Include its intent in `description`; do not create empty stubs.

```bash
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks list --session CH-000465
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks create --session CH-000465 --id auth-refactor --title "Auth refactor" --description "Ship the auth fix" --items '[{"id":"api","text":"Fix API"},{"id":"test","text":"Verify"}]'
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --set-done api
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --ops '{"add":[{"item":{"id":"docs","text":"Update docs"}}],"description":"Validation remains"}'
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks get auth-refactor
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks history auth-refactor
```

Flags go after the verb. JSON output; exit `0` success, `2` invalid/not found.
Inside a Cuttle harness, `--session` inherits the current chat automatically.
Outside one, supply `--session CH-…` and optionally `--actor-agent codex`.
Use the Cuttle venv and src path from this runbook, also for guest projects.

Default scope is `session`; `--scope project` shares a list with chats belonging
to the same project. `--edit-mode shared` lets users toggle items; default
`agent` reserves task edits for agents. Items may have nested `children`.
Completing every item auto-archives; undoing an item or adding open work reopens
the list. `--status archived` manually archives. Patch supports `set_done`,
`set_undone`, `set_text`, `add`, `remove`, `items`, `description`, `title`,
`scope`, and `edit_mode`; scope changes require `--session`.

Tasks render in the composer dock rather than the install-wide usage docks.
They do not require the experimental flag. This preserves existing behavior;
all existing lists remain in the same auth-owned rows, with no state copy.
The new owner is `api.gizmos.tasks`, with pure rules in `tasks_model` and its
CLI/HTTP transports alongside it. Legacy widget endpoints/tags are compatibility
paths, not the agent workflow. Clients refresh from a monotonic write sequence,
including archives, while the agent is working.

Authenticated REST (each caller can access only their own tasks/chats):

- `GET /api/gizmos/tasks?session_id=465` → `{success, gizmos, revision}`
- `POST /api/gizmos/tasks` `{id?, session_id, title?, items, description?, scope?, edit_mode?}`
- `GET|PATCH /api/gizmos/tasks/<id>` → `{success, gizmo}`; PATCH accepts task ops and `session_id`
- `PATCH /api/gizmos/tasks/<id>/items/<item_id>` `{done: true, session_id}` (shared lists)
- `GET /api/gizmos/tasks/<id>/history?limit=100` → `{success, events}`

## Interaction attribution

Every Tasks or usage-gizmo create/update/move/archive/delete writes a durable
log alongside the state change in the same SQLite transaction. Logs include
operation, timestamp, user, chat, agent, model, run/query id and before/after
state. Historical changes before this feature have no retroactive attribution.
Explicit agent inspection is logged too; background UI polling is excluded.

The harness supplies `CUTTLE_CHAT_SESSION_ID`, `CUTTLE_AGENT_ID`,
`CUTTLE_AGENT_MODEL`, `CUTTLE_AGENT_RUN_ID`, and `CUTTLE_AGENT_PROJECT_PATH` in
its child environment, isolated per turn (never process-global mutations).
Standalone invocations record the identity they have; missing identity remains
empty rather than guessing a chat or agent. Browser edits record the authenticated
user and originating chat. The Tasks History button shows recent attribution. Authenticated Tasks API agents may declare
`actor: {agent_id, model, run_id}` (or `X-Cuttle-Agent`, `X-Cuttle-Model`,
`X-Cuttle-Run` headers for authenticated agent reads); this is caller-reported
provenance, and cannot
override the authenticated user or validated `session_id`.

Inspect Tasks logs with `tasks history <id>`, and usage-gizmo logs with
`python -m api.gizmos history <id>` or `GET /api/gizmos/<id>/history` (owner only).
Usage-gizmo history survives removal. Tasks logs live in the auth database with
the scoped lists; usage logs live in `gizmos.db`, both under the Cuttle home.


## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.gizmos list
```
