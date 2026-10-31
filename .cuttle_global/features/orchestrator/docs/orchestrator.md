# Orchestrator

Flag `orchestrator`. A **playbook** is a saved orchestration setup: an ordered
org chart of tiers (worker → escalation → expert), each a list of agents — a
saved **agent profile** or a **temporary agent** (harness · model · effort
pinned in the playbook, no profile); each agent can be switched off without
removing it, and a tier with every agent off is skipped. Plus grind/retry
limits, supervisor defaults and free-form notes. Users edit playbooks in
**Apps → Orchestrator** (duplicate, make default) and profiles in **Apps → Agents**.

## Start

`/orchestrate [@playbook] <goal>` binds the chat and prints the brief. Without
`@playbook` it uses the user's default playbook. In the composer the command is
a chip (`Orchestrate · <playbook>`); clicking it picks the playbook. Or:

```bash
PYTHONPATH=src .venv/bin/python -m api.orchestrator start [--playbook ID] --goal "…" --json
PYTHONPATH=src .venv/bin/python -m api.orchestrator default [ID]     # show / set the default
PYTHONPATH=src .venv/bin/python -m api.orchestrator brief            # reprint for this chat
PYTHONPATH=src .venv/bin/python -m api.orchestrator stop             # unbind
PYTHONPATH=src .venv/bin/python -m api.orchestrator list|get <id>|bindings --json
```

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

## Supervisor

Supervision is started **from the supervisor chat** (`chat-history.md`). Get
the playbook's command for a target, with its branch/path guards and model
tiers filled in:

```bash
PYTHONPATH=src .venv/bin/python -m api.orchestrator supervise-command CH-<orchestrator> --json
```

Run the returned `command` from the supervisor chat. It omits `--agent` unless
the playbook pins one, so check-ins follow that chat's own agent.

## Editing

Profiles: `python -m api.subagents profiles save --id … --agent … --model … --effort …`
(validated the same way as spawn). Playbooks: Apps → Orchestrator, or
`python -m api.orchestrator save --file playbook.json`. Prefer the Apps when the
user is changing their setup.
