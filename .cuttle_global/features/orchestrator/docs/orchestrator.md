# Orchestrator

Flag `orchestrator`. A **playbook** is a saved orchestration setup: an ordered
org chart of tiers (worker → escalation → expert), each a list of **agent
profiles**; grind/retry limits; supervisor defaults; free-form notes. Users edit
playbooks in **Apps → Orchestrator** and profiles in **Apps → Agents**.

## Start

`/orchestrate [@playbook] <goal>` binds the chat and prints the brief. Or:

```bash
PYTHONPATH=src .venv/bin/python -m api.orchestrator start --playbook default --goal "…" --json
PYTHONPATH=src .venv/bin/python -m api.orchestrator brief            # reprint for this chat
PYTHONPATH=src .venv/bin/python -m api.orchestrator stop             # unbind
PYTHONPATH=src .venv/bin/python -m api.orchestrator list|get <id>|bindings --json
```

Inside a Cuttle turn `--session` defaults to the current chat. A bound chat's
brief is part of its compiled context, so a new harness joining the chat gets
it too. Treat it as standing instructions.

## Delegating

Spawn with the profile id; the profile pins harness, model and effort:

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
