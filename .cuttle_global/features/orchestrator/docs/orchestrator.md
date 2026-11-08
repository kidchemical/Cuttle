# Orchestrator

Flag `orchestrator`. A **playbook** is a saved orchestration setup: an ordered
org chart of tiers (worker → escalation → expert), each a list of agents — a
saved **agent profile** or a **temporary agent** (harness · model · effort
pinned in the playbook, no profile); each agent can be switched off without
removing it, and a tier with every agent off is skipped. Plus grind/retry
limits, supervisor defaults and free-form notes. Users edit playbooks in
**Apps → Orchestrator** (duplicate, make default) and profiles in **Apps → Agents**.

## Start

The binding (chat → playbook + goal) is **host state**; agents never set it.

- **☰ → Orchestrate this chat…** picks the playbook and goal, then sends
  `/orchestrate @<playbook> <goal>` as the chat's first turn on its own agent.
- Typed `/orchestrate [@playbook] <goal>` does the same: the command's
  `host_step: orchestrator.bind` binds the chat on the server **before** the
  agent runs and appends one line naming the playbook. Without `@playbook` it
  uses the user's default; with no goal on an unbound chat Cuttle answers by
  itself (no agent turn); with no goal on a bound chat it switches playbook and
  keeps the goal. In the composer the command is a chip (`Orchestrate · <playbook>`).
- A bound chat shows **Orchestrating · \<playbook\>** in its header: Edit…
  (switch playbook / edit goal), Open playbooks, Stop orchestrating. A switch is
  not an agent turn: the brief changes at the next turn (resume delta), and each
  supervision schedule on the chat takes the new playbook's supervisor settings
  with one **Supervision update** check-in, as for a playbook edit.
- Every bind / switch / goal edit / unbind is logged (`binding_events` in
  `orchestrator.db`) with its source (`ui`, `command`, `cli`).

```bash
PYTHONPATH=src .venv/bin/python -m api.orchestrator switch [--playbook ID] [--goal "…"]   # change a bound chat
PYTHONPATH=src .venv/bin/python -m api.orchestrator default [ID]     # show / set the default
PYTHONPATH=src .venv/bin/python -m api.orchestrator brief            # reprint for this chat
PYTHONPATH=src .venv/bin/python -m api.orchestrator stop             # unbind
PYTHONPATH=src .venv/bin/python -m api.orchestrator list|get <id>|bindings --json
PYTHONPATH=src .venv/bin/python -m api.orchestrator start [--playbook ID] --goal "…"   # scripts only; not from an agent turn
```

REST: `GET|PUT|DELETE /api/orchestrator/bindings/<session>` (PUT
`{playbook_id?, goal?}`; omitted fields are kept).

Inside a Cuttle turn `--session` defaults to the current chat. A bound chat's
brief is part of its compiled context, so a new harness joining the chat gets
it too. Treat it as standing instructions.

## Delegating

Spawn with the profile id; the profile pins harness, model and effort. A
temporary agent's brief cell shows the inline `profile` object to send instead:

```bash
PYTHONPATH=src .venv/bin/python -m api.subagents spawn --parent CH-… --wait --json \
  --child "{\"title\":\"…\",\"profile\":\"grunt\",\"message\":\"…\"}"
```

Spawn checks every child's harness/model/effort against that CLI's catalog
before any chat is created. A rejection names the child and the bad pin: fix
the spec or use the tier's next profile — never resend it unchanged.

**Long-lived lanes fill up.** Before reusing a conversational child for its
next task, check `subagents status --parent CH-… --context --json`; above ~75%
run `python -m api.chat_cli compact CH-<child>` between its turns, or
`--mode fresh` to restart it on a clean session (it keeps its chat, links and
shared Tasks list; the briefing and recent transcript are re-sent). One-shot
children need neither.

## Supervisor

Supervision is started **from the supervisor chat** (`chat-history.md`). Get
the playbook's command for a target, with its branch/path guards and model
tiers filled in:

```bash
PYTHONPATH=src .venv/bin/python -m api.orchestrator supervise-command CH-<orchestrator> --json
```

Run the returned `command` from the supervisor chat. It omits `--agent` unless
the playbook pins one, so check-ins follow that chat's own agent.

## Playbook edits while it runs

A bound orchestrator gets the edited brief at its **next turn** (resume delta
"Orchestrator playbook updated"); a turn already running keeps the old one.
Saving the playbook also updates each supervision schedule on a bound chat:
fields still matching the old playbook (interval, instructions, guards, model
guidance) take the new values, fields set on the schedule itself are kept, and
the supervisor gets one **Supervision update** check-in naming the change. On
that check-in, steer the orchestrator only if its live turn is doing work the
edit affects; otherwise end the turn.

## Editing

Profiles: `python -m api.subagents profiles save --id … --agent … --model … --effort …`
(validated the same way as spawn). Playbooks: Apps → Orchestrator, or
`python -m api.orchestrator save --file playbook.json`. Prefer the Apps when the
user is changing their setup.
