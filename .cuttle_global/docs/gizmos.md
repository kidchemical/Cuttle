# Tasks gizmos

Tasks are established chat functionality, available independently of experimental features.

## Tasks gizmos (composer dock)

For multi-step work, **create a Tasks gizmo at the beginning of planning**, then
patch the same id as work progresses. These are tool calls: do not emit
`<cuttle_widget>` Tasks tags or a second list in a final reply. First inspect
active lists from the context digest or `tasks list` and reuse the list for the
same concern. Include its intent in `description`; do not create empty stubs.

Use Tasks for an agent's multi-step investigation, implementation, or other
work, even when the user does not ask for a progress display. Skip a quick
answer or small edit. Tasks show completed steps, not a live job progress bar:
for a long-running OS job, also use a status-backed watch card as described in
`action-forms.md`. Do not fabricate percentages for work without a measurable
progress source.

```bash
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks list --session CH-000465
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks create --session CH-000465 --id auth-refactor --title "Auth refactor" --description "Ship the auth fix" --items '[{"id":"api","text":"Fix API"},{"id":"test","text":"Verify"}]'
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --set-done api
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --ops '{"add":[{"item":{"id":"docs","text":"Update docs"}}],"description":"Validation remains"}'
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks get auth-refactor
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks history auth-refactor
PYTHONPATH=src .venv/bin/python -m api.gizmos tasks patch auth-refactor --link CH-000470,CH-000471 --unlink CH-000468
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

- `GET /api/gizmos/tasks?session_id=465` → `{success, gizmos, revision}` (home, project and linked lists)
- `GET /api/gizmos/tasks?all=1` → `{success, lists, chats, revision}`: every list (active + archived) with `home_chat`/`linked_chats`, for the Tasks App
- `POST /api/gizmos/tasks` `{id?, session_id, title?, items, description?, scope?, edit_mode?, linked_sessions?}`
- `GET|PATCH|DELETE /api/gizmos/tasks/<id>` → `{success, gizmo}`; PATCH accepts task ops, `session_id`, and
  `expected_revision` (409 `conflict` when the list changed since it was read); `?summary=1` adds chat names
- `GET /api/gizmos/tasks/<id>/related` → orchestration-family chats (sub-agent parent/children, supervisors)
- `PATCH /api/gizmos/tasks/<id>/items/<item_id>` `{done: true, session_id}` (shared lists)
- `GET /api/gizmos/tasks/<id>/history?limit=100` → `{success, events}`

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
