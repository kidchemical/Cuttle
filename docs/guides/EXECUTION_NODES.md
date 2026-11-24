# Execution nodes

**Status:** experimental (`exec_nodes`, default off). Agent runbook:
[`.cuttle_global/features/exec_nodes/docs/execution-nodes.md`](../../.cuttle_global/features/exec_nodes/docs/execution-nodes.md).
Related: [`CUTTLE_WORKERS.md`](CUTTLE_WORKERS.md) (the mesh this rides on).

Any chat can run its agent CLI on any machine paired with the Cuttle host. The
host stays authoritative for accounts, chat history, databases, settings and
context; a node contributes its CPU/GPU, files, repositories, installed tools and
its own CLI login. A node is not a second host: it runs the client daemon only
and keeps no chat database.

## Why the mesh, and what is new

Adopt / adapt / build, recorded before the code:

| Need | Existing piece | Decision |
|---|---|---|
| Machine identity, pairing, revocation | Device-worker enrollment: owner-approved pairing, per-device bearer bound to the worker id, pinned HTTPS (`api.device_workers`) | **Adopt.** A node is a paired worker. No new credential. |
| Liveness, capability ads | Worker heartbeat + capability blob | **Adapt.** Two keys added: `agent_exec`, `agents`. |
| Getting work to a machine that only dials out | Claim / lease job queue | **Adapt.** One job type, `agent_turn` (reserved in `CUTTLE_WORKERS.md` since W0). |
| Running the vendor CLI | Harness adapters (`api.agent_harness`) | **Adopt unchanged.** The node runs the same adapter the host would. |
| Running a CLI on another machine over SSH | The user's OpenSSH (`remote-access.md`) | **Not used.** Adapters resolve the executable, write temp files and read the CLI's session store locally; wrapping eight of them in `ssh` would fork each one. SSH stays the tool for ad-hoc commands. |
| Streaming a turn back | — | **Build**, small: two relay routes and one SQLite relay table. |

What a chat adds on top of the mesh is one fact: which node it runs on.

## Flow

```text
chat turn (any surface)                         host                                   node
  kernel.run_agent_web_command ── adapter_for_turn(chat) ──► RemoteAdapter.execute
     context compile, query log,                   │  open relay row (payload)
     resume ids, reply save: all host               │  submit agent_turn job (metadata only, targeted)
                                                    │◄── claim (device bearer) ─────── worker loop
                                                    │◄── GET  /api/workers/exec/turns/<job> ── payload
                                                    │◄── POST …/turns/<job>/events ─── status lines, keepalive
                                                    │ ── {"cancel": bool} ───────────► Stop reaches the CLI
                                                    │◄── POST …/events {result} ────── adapter result
     AgentResult ◄──────────────────────────────── close relay row                      complete job
```

The kernel has one call site (`adapter_for_turn`). Everything else about a turn
(context compiler, handoff, query tracking, usage, reply persistence, supervision,
sub-agent child turns) is the ordinary path, which is why supervised and
orchestrated chats and sub-agents work without their own remote code.

## Contract v1 (`api.exec_nodes.model`)

- Mesh job `agent_turn`: `params = {agent_id, chat, cwd}`. Never the prompt or the
  reply. Targeted at one worker, requires capability `agent_exec`, one attempt,
  never requeued (a rerun would repeat the agent's work). Not accepted by
  `POST /api/workers/jobs`: only a chat turn on the host creates one.
- Payload (node fetches it once): `{contract, agent_id, prompt, cwd, resume, model,
  effort, timeout, chat}`.
- Events (node → host): plain status strings. Entries that steer the host's turn
  pump are never relayed, in either direction.
- Result: the adapter's `AgentResult` fields; node-local extras are dropped.
- A node that speaks another contract version refuses the turn with a clear error.

## State (all on the host, `<home>/db/exec_nodes.db`)

| Table | Lifetime | What |
|---|---|---|
| `chat_nodes` | persistent | chat → node id + working directory on that node |
| `node_prefs` | persistent | label, default directory, host-path → node-path map |
| `resume` | persistent | the CLI session id a chat's agent holds on each node |
| `last_node` | persistent | where each agent of a chat last ran (detects a move) |
| `turns`, `turn_events` | one turn | the relay between Flask (node posts) and the process waiting on the turn, which may be Flask or a sub-agent CLI; deleted when the turn ends |

Nothing is stored on the node except what the vendor CLI keeps for itself.

## Context

- The prompt is compiled once, on the host, and sent as is: no second history, no
  extra instruction layer. A fresh CLI session on a node gets one added line that
  says which machine it is on.
- Host paths inside the Cuttle context are rewritten to the node's copy where the
  node has a path map for them. What the user typed is never rewritten.
- Model and effort: the host resolves the chat's pins and starred defaults and
  sends them as explicit overrides (a node knows nothing of the chat's pins).
- Switching a chat's node: the agent's native session is on the other machine, so
  the next turn carries the recent conversation (the handoff's existing
  workspace-reset path) and a full briefing. Switching back resumes the session
  that machine already has, plus the same catch-up. History is never copied.

## Safety

- A switch is read when a turn starts. It never interrupts a running turn.
- A new chat has no id until its first message, so a pick made before it rides
  that message (`exec_node` in the `/api/chat` body) and `service.bind_new_chat`
  stores it as the chat is minted, ahead of the turn. Owner-only, like the PUT.
- Node offline, agent execution off there, or CLI missing: the turn does not run
  and the reply says why.
- Node never claims (busy or gone): the host cancels the job after 90 s.
- Node goes silent mid-turn: after 75 s the host ends the turn with an explicit
  error. Work the agent finished stays on the node; its CLI session can be resumed.
- Host unreachable from the node for 2 min, or the host dropped the turn: the node
  stops the CLI.
- Stop: the node's CLI is cancelled and the processes that turn started there are
  ended (found by the turn's run marker, so detached ones too).
- Authorization: choosing a node is owner-only and checked against chat ownership.
  The relay accepts only the device bearer of the node the turn was issued to.
  A machine runs agent turns only after opting in locally
  (`device_workers.agent_exec.enabled` in its settings, or `CUTTLE_NODE_AGENT_EXEC=1`
  in its `<home>/.env`); pairing as a worker alone grants nothing.
- No credential leaves the host. Host API-credit billing (`/use-api`) does not
  apply to node turns.

## Setting up a node

On the machine (Windows or Linux), with a Cuttle source checkout at the host's revision:

1. Pair it: connect the Cuttle Client to the host once and approve the code in
   Jobs → Devices.
2. Run the client daemon (`src/scripts/cuttle_client_daemon.py`); the Electron
   sidecar alone cannot run agent turns.
3. Opt in: add `CUTTLE_NODE_AGENT_EXEC=1` to that machine's `<home>/.env`
   (`%LOCALAPPDATA%\Cuttle\.env` on Windows) and restart the daemon.
4. Install and log in the agent CLIs you want there.

On the host: enable **Execution nodes** in Settings → Experimental, then tell
Cuttle where projects live on the node:

```bash
PYTHONPATH=src .venv/bin/python -m api.exec_nodes node kcslaptop \
  --default-cwd 'C:\Users\me' --map '/mnt/dev/Cuttle=C:\Users\me\Dev\Cuttle'
```

## Limits (v1)

- Cuttle verbs (`python -m api.*`: Tasks, sub-agents, chat history) are host-only,
  so an orchestrator belongs on the host and assigns workers to nodes
  (`"node"` in a child spec). There is no automatic scheduler.
- Mid-turn steering and background detach are host-only: a node turn behaves like
  an agent without steering (a message sent while it runs waits for the turn to end).
- The query inspector shows the node turn's status lines, not per-tool payloads;
  edit attribution, Keep/Undo review and auto-commit see only the host's files.
- Attachments reach the agent as the host's text digest; files are not copied.
- The router picks agents by what the host has installed; a pick whose CLI is
  missing on the node fails with that message.
- A node claims work on its poll interval (5 s default), so a turn starts a few
  seconds later than on the host. While the node runs a long mesh job (a render),
  new turns wait for it.
- A node's leftover processes are not listed in the host's process views.

## Verify

- Unit + relay: `src/tests/exec_nodes/` (fake adapter, real routes and stores).
- Browser: `src/tests/e2e/test_chat_exec_node.py`.
- Real CLI, deliberate (spends a few cheap-model turns, isolated from the running
  Cuttle): `PYTHONPATH=src .venv/bin/python src/scripts/exec_node_smoke.py`.

## Removal

Delete the `exec_nodes` flag row, `.cuttle_global/features/exec_nodes/`, `src/api/exec_nodes/`, the `adapter_for_turn`
call in `agent_harness/kernel.py`, the `bind_new_chat` call in `web_chat_api.py`, the `agent_turn` branches in
`device_workers` (`executor`, `worker_loop`, `capabilities`, `config`,
`job_policy`, `client`, the nested blueprint in `routes`), `ChildSpec.node`,
`handoff.mark_workspace_moved`, the two `chat_exec_node*.js` files with their
script tags, slot and `chat_page.js` call site. `exec_nodes.db` can be deleted;
chats then run on the host, with their history intact.
