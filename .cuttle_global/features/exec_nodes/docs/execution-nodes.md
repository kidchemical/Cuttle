# Execution nodes (experimental `exec_nodes`)

A chat's agent CLI can run on another machine paired with this Cuttle host. The
chat, its history, settings and context stay on the host; the node runs the CLI
with its own files, tools, GPU and CLI login, and streams status and the reply
back. Not for renders, file copy or Client updates (`cuttle-workers.md`), and not
for one ad-hoc command on another machine (`remote-access.md`).

Owner: `src/api/exec_nodes/`. Design and limits: `docs/guides/EXECUTION_NODES.md`
in the Cuttle install.

## Verbs

```bash
PYTHONPATH=src .venv/bin/python -m api.exec_nodes list [--agent claude]      # host + paired machines, state
PYTHONPATH=src .venv/bin/python -m api.exec_nodes show [--session CH-900008]  # where a chat runs
PYTHONPATH=src .venv/bin/python -m api.exec_nodes set kcslaptop [--cwd 'C:\Users\me\Dev\proj'] [--session CH-900008]
PYTHONPATH=src .venv/bin/python -m api.exec_nodes set host                     # back to the host
PYTHONPATH=src .venv/bin/python -m api.exec_nodes node kcslaptop --default-cwd 'C:\Users\me' \
  --map '/mnt/dev/Proj=C:\Users\me\Dev\Proj'                                   # where host projects live there
```

`--session` defaults to the current chat. A change applies from the chat's next
turn. A running turn finishes where it started. In the chat menu, choose
**Execution node…** to pick a machine or return to the host. Chats assigned to
a paired machine also show a node badge in the header.

States: `online`, `busy` (a turn is running there; a new one waits for a slot),
`unavailable` (offline, agent execution off on that machine, or the agent's CLI
is not installed there). A turn sent to an unavailable node does not run and
says so.

## Sub-agents on a node

Give a child its machine in the spawn spec; unset means the host:

```bash
--child "{\"title\":\"GPU bake\",\"agent\":\"codex\",\"node\":\"kcslaptop\",\"message\":\"…\"}"
```

Spawn from a chat that runs on the host: a child on a node cannot spawn its own
children (see below).

## When your turn runs on a node

The first prompt of a node session says so. There:

- Paths in the Cuttle context that are mapped for the node point at its copy; other
  host paths (the Cuttle home, chat database, `/output/shared`) do not exist.
- `python -m api.*` verbs act on the host and are not reachable. Do the file and
  tool work; report in the reply. Tasks lists, sub-agents, chat history and action
  side effects belong to a chat on the host.
- Cuttle tags in the reply (`cuttle_action_form`, media links to files you can name
  by URL) still render: the reply is delivered by the host.

## Do not

- Copy CLI credentials, tokens or `.env` files to a node: each machine logs its CLI in itself.
- Use a node for a turn that needs the host's Cuttle verbs or its Cuttle home.
- Start a second Cuttle host on the node: it only runs the client daemon.
