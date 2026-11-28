# Tasks gizmos

Chat handles in the examples are fictional. Substitute handles from the current installation.

Tasks are established chat functionality, available independently of experimental features.

## Tasks gizmos (composer dock)

For multi-step work, **create a Tasks gizmo at the beginning of planning**, then
patch the same id as work progresses. These are tool calls: do not emit
`<cuttle_widget>` Tasks tags or a second list in a final reply. First inspect
active lists from the context digest or `tasks list` and reuse the list for the
same concern. The digest is a directory (title, id, progress, intent, plus the
next open items of this chat's own list): run `tasks get <id>` for the items. Include its intent in `description`; do not create empty stubs.

Use Tasks for an agent's multi-step investigation, implementation, or other
work, even when the user does not ask for a progress display. Skip a quick
answer or small edit. Tasks are the **agent-step progress contract**: creating
early stamps `origin_turn_id` so Studio's under-bubble work panel shows the
checklist (with a list-level done/total bar) on that turn; background completion
notices append the next open item so the wake turn can continue without the
user asking. Tasks show completed steps, not a live OS job percent bar: for a
long-running OS job with a measurable status producer, also use a status-backed
watch card as described in `action-forms.md`. Do not fabricate per-item
percentages for work without a measurable progress source.

**Mark the item you are on.** When you start an item, patch it with
`--working <item>`; when it is finished, `--set-done <item>` (which also clears
the working mark). One call can do both: finish one item and start the next.
The chat shows the working item as in progress, so the user sees where you are.
Use `--not-working <item>` if you set it aside unfinished.

**Give every item a short `label`.** `text` is the full step (the Tasks dock
shows it); `label` is what the checklist in your chat bubble shows, on one line
on a phone: 1–3 words, at most 32 characters, e.g. `"Lua API"` for "Lua API:
breakable world object, forced scroll, bound scrolls". Longer labels are
rejected. Set it on create/add, or later with `--label <item>=<label>`
(op `set_label {item: label}`; an empty label clears it).

```bash
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks list --session CH-900004
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks create --session CH-900004 --id auth-refactor --title "Auth refactor" --description "Ship the auth fix" --items '[{"id":"api","text":"Fix the token refresh in the API client","label":"Fix API"},{"id":"test","text":"Run the auth tests and a manual login","label":"Verify"}]'
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --working api
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --set-done api --working test
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --ops '{"add":[{"item":{"id":"docs","text":"Update the auth docs","label":"Docs"}}],"description":"Validation remains"}'
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --label api="Token refresh"
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --hide api --unhide test
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks get auth-refactor
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks history auth-refactor
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --link CH-900006,CH-900007 --unlink CH-900005
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
`set_undone`, `set_text`, `set_label`, `add`, `remove`, `items`, `description`, `title`,
`scope`, `edit_mode`, `set_hidden`, and `clear_hidden`; scope changes require `--session`.

**Hide items from the chat UI.** `--hide <item>` / `--unhide <item>` (ops
`set_hidden` / `clear_hidden`) mark an item so the Tasks panel and work checklist
omit it; `tasks get` and the agent context digest still include it. Checking an
item done never hides it — hiding is strictly opt-in. The user can also toggle
visibility with the eye control in the Tasks dock / Tasks App (works on
agent-managed lists too). When a list has hidden items, the panel shows
`Show hidden (N)`.

**Linked chats.** Besides its home chat (or project), a list can be linked to
specific chats: `--link CH-…` on `create`/`patch` (repeatable or comma-separated),
`--unlink CH-…` to drop one; ops `link`, `unlink`, `linked_sessions` (replace).
Linked chats show the list in their composer and in their agents' context
digest, and can patch it like the home chat. An orchestrator should link its
plan to its supervisor and sub-agent chats instead of creating a list per chat;
sub-agents then check off their items on the shared list. Links must be the
same user's chats. The **Tasks App** (Apps → Tasks) suggests links from the
home chat's sub-agent parent/children and live supervision schedules.

Tasks render in the composer dock rather than the install-wide usage docks.
They do not require the experimental flag. This preserves existing behavior;
all existing lists remain in the same auth-owned rows, with no state copy.
The new owner is `api.gizmos.tasks`, with pure rules in `tasks_model` and its
CLI/HTTP transports alongside it. Legacy widget endpoints/tags are compatibility
paths, not the agent workflow. Clients refresh from a monotonic write sequence,
including archives, while the agent is working.

Authenticated REST (each caller can access only their own tasks/chats):

- `GET /api/gizmos/tasks?session_id=900004` → `{success, gizmos, revision}` (home, project and linked lists)
- `GET /api/gizmos/tasks?all=1` → `{success, lists, chats, revision}`: every list (active + archived) with `home_chat`/`linked_chats`, for the Tasks App
- `POST /api/gizmos/tasks` `{id?, session_id, title?, items, description?, scope?, edit_mode?, linked_sessions?}`
- `GET|PATCH|DELETE /api/gizmos/tasks/<id>` → `{success, gizmo}`; PATCH accepts task ops, `session_id`, and
  `expected_revision` (409 `conflict` when the list changed since it was read); `?summary=1` adds chat names
- `GET /api/gizmos/tasks/<id>/related` → orchestration-family chats (sub-agent parent/children, supervisors)
- `PATCH /api/gizmos/tasks/<id>/items/<item_id>` `{done: true, session_id}` (shared lists);
  `{hidden: true|false, session_id}` toggles visibility on any list the user owns
  (including agent-managed)
- `GET /api/gizmos/tasks/<id>/history?limit=100` → `{success, events}`

### Owners and work in progress

An item can name who holds it and whether it is being worked on. `owner` is
`{"kind":"chat"|"agent"|"user"|"group","id":"CH-…","label":"…","agent":"codex"}`:
`id` is the chat handle for kind `chat` (must be one of your chats), `label` is
plain text up to 60 characters, `agent` is a harness id. `in_progress` is a flag;
a done item is never in progress. Colours and markup are not stored.

```bash
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --owner api=CH-900006 --working api
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --owner test=user --owner docs=agent:claude
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --not-working api --owner api=none
```

Patch ops: `set_owner {item: owner|null}`, `set_in_progress [ids]`,
`clear_in_progress [ids]`, `set_hidden [ids]`, `clear_hidden [ids]`; `set_done`
clears `in_progress` but leaves `hidden` alone. The item route
`PATCH /api/gizmos/tasks/<id>/items/<item>` accepts `owner`, `in_progress` and
`hidden` (`done` stays required unless one of them is sent). A sub-agent spawned with
`"task":"<gizmo>:<item>"` sets that item's owner and in-progress flag (see
`subagents.md`). `origin_turn_id` records the chat turn that created the list;
it is read-only.

## Interaction attribution

Every Tasks create/update/archive/delete writes a durable
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

Inspect Tasks logs with `tasks history <id>`.
