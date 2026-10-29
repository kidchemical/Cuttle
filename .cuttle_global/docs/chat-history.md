# Cuttle chat history

Transcripts live in a **gitignored SQLite DB**, not in the working tree. Empty
`rg`/`grep` over the repo is **not** evidence that Cuttle has no chats — never
answer “no traces of Cuttle chats found” on that basis alone.

Open this file before reading or summarizing chat history.
Prefer `<home>/personal/docs/chat-history.md` when it exists (install-local DB path).
`<home>` is the per-user Cuttle home (`~/.local/share/cuttle`, `%LOCALAPPDATA%\Cuttle`, or `$CUTTLE_HOME`; `core.runtime_paths.cuttle_home()`).

## Agent ops CLI (preferred)

Do **not** hand-roll SQL against `cuttle_auth.db`. Use the CLI (wraps the same
library the UI uses for share indices):

```bash
# From Cuttle repo root — always use the project venv (bcrypt + api on path)
PYTHONPATH=src .venv/bin/python -m api.chat_cli get CH-000430-99 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli get CH-000430 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli session CH-000430 --limit 40 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli session CH-000430 --all --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli search "flask restart" --from-chat CH-000465 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli parse CH-000430-99 --json
```

Do **not** call a bare `python` / system interpreter — missing venv deps used to
look like “chat_cli is broken” (e.g. `ModuleNotFoundError: bcrypt`) even though
reads no longer import bcrypt at module load.

Put flags **after** the verb (`get … --json`, not `--json get …`).

`get` / `session` / `search` hide other agents' `<think>` narration and
post-processing originals (they trip provider reasoning-extraction safeguards
for the agent reading them). Read through `chat_cli`, not raw SQL; `--raw`
shows stored text unchanged, but only for debugging storage, never to bring
context into your own turn.

| Verb | Use |
|---|---|
| `get CH-…` | Session summary, or one bubble when `CH-…-N` |
| `session CH-…` | List bubbles with UI share indices |
| `search "…"` | Title/body search (pass `--from-chat` on multi-user DBs) |
| `parse CH-…` | Parse handle only (no DB) |
| `status` / `snapshot` / `send` / `stop` / `supervise` | Operate on another chat. See [Talk to, inspect, or supervise an existing chat](#talk-to-inspect-or-supervise-an-existing-chat) |

Exit codes: `0` ok, `2` not found / bad handle, `3` other error.

Library (same semantics): `resolve_chat_handle_message`, 
`AuthDatabase.get_message_by_share_index`. Design pattern: `.cuttle_global/docs/agent-ops-cli.md`.

## Talk to, inspect, or supervise an existing chat

Same CLI, one call each. Never mint auth tokens, curl `/api/chat`, or read
Flask source for this. `api.subagents message` only reaches child chats it
spawned. Use these verbs for any other chat (including a parent or sibling).
The acting chat defaults to `CUTTLE_CHAT_SESSION_ID` (already set inside a
Cuttle turn); pass `--as CH-…` otherwise. Both chats must have the same owner.

```bash
PYTHONPATH=src .venv/bin/python -m api.chat_cli status   CH-001548 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli snapshot CH-001548 --usage codex --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli send     CH-001548 --text "Continue with the next open task." --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli stop     CH-001548 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli supervise CH-001548 --every 900 --agent cursor \
  --instructions "Keep it on its Tasks list; nudge it when it stalls." \
  --require-branch dev/multiplayer --forbid-branch dev/core --forbid-branch master \
  --model-tiers '{"worker":["cursor auto","codex terra medium"],"expert":["claude opus"]}' --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli operations --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli cancel-operation <id> --json
```

| Verb | Use |
|---|---|
| `status CH-…` | `state`: `busy`, `idle`, `idle_with_work` (open Tasks items), `awaiting_input` (form waiting), `error`, `cancelled`; plus Tasks progress, follow-up queue, latest reply (`latest_reply.text` is the readable answer: post-processing wrapper and `<think>` removed, cards as `[card: …]`; `content` is the raw stored row) |
| `snapshot CH-…` | `status` plus worktree binding, live branch, execution path, the chat's jobs and watch bars; `--usage <agent>` (repeatable) adds plan-usage windows |
| `send CH-… --text` | A live turn is steered (`delivery: steered`). Otherwise it is queued in a host-owned outbox (`delivery: queued`) and sent when the chat goes idle. The browser does not need to be open. `--mode queue` never steers |
| `stop CH-…` | Same as the Stop button |
| `supervise CH-…` | Durable check-ins that survive your turn and Flask restarts. Each interval the host checks the target. It wakes **your** chat with a compact report only when the target is not busy and something changed, or when a branch/path rule is violated. A healthy, working target costs no tokens |
| `supervise … --agent ID` | Prefix each check-in with `/<agent>` so that harness runs it (validated against the agent catalog) |
| `operations` / `operation <id>` / `cancel-operation <id>` | Inspect or cancel your queued sends and supervision schedules |
| `pause-operation <id>` / `resume-operation <id>` | Pause a schedule; resume checks right away |

Send states: `pending` → `delivered` (host started the turn) / `done` /
`failed`. If the connection drops mid-send (for example, Flask restarts), the
send goes to `verifying`. Once the chat is idle, the host looks in its history
for the send's id (`metadata.chat_op_id` on the user row). If the id is there,
the send is `delivered`. If not, it was never received and is resent
automatically. A message is never sent twice. Sends, steers and supervision
check-ins render in the receiving chat as **Cuttle** (like a parent's message
to a sub-agent), not as the user. This happens only when the chat-ops store
holds that id for that chat. `unknown` now means three
attempts never reached the chat; run `status` before resending by hand.

**Supervising another chat:** run `status` once to orient, then `supervise`,
then end your turn. Do not sleep-loop inside a turn. Each check-in arrives as
a new turn in your chat. Act on the target with `send` (steer or queue). Do not
message its sub-agent children unless asked.

Sends and supervision need the host up (Flask on loopback); read-only `get` /
`session` / `search` do not. Library: `api.chat_ops` (service, store, worker).

## This chat vs other chats

- Display id `CH-<zero-padded number>` → numeric `chat_sessions.id` (strip `CH-` and leading zeros).
- Message ref `CH-<session>-<N>` = **same** session, 1-based bubble index `<N>` among
  **user+assistant rows only** (`role IN ('user', 'assistant')`; exclude `role='system'`),
  ordered by `chat_messages.id` (not the row primary key). System notices — especially
  **Stop generating** (`⏹ Stopped generating.`) — are stored in the DB but **do not**
  consume a share index. Matching the UI: `.message:not(.system)` / message-nav.
- Never invent a different meaning for `CH-<session>-<N>` (it is not a second chat).
- Never reuse a session id from an example or from this doc’s sample numbers — that
  reads a stranger’s conversation. Use the id named in your turn briefing, or the
  `CH-` handle the user gave you.
- **In replies:** cite as bare `CH-000182` (Cuttle linkifies the badge). Do not wrap
  in `[CH-…](file://…)` — that becomes a file chip. Rule: `.cuttle_global/rules/01-chat-handles.md`.

## Database

| | |
|---|---|
| Default path | `<home>/db/cuttle_auth.db` (resolve via runtime / `api.auth_db.DB_PATH`) |

Tables: `chat_sessions(id, user_id, session_name, last_activity, is_active)`,
`chat_messages(chat_session_id, role, content, timestamp)`.

## Live API (host)

Prefer the panes CLI over hand-rolled curl:

```bash
PYTHONPATH=src .venv/bin/python -m api.panes_cli list --json
PYTHONPATH=src .venv/bin/python -m api.panes_cli messages 1 --limit 40 --json
```

(Flask must be up — HTTPS on `127.0.0.1:8080`, self-signed.)

Raw endpoints (fallback): `GET /api/shell/panes`, `GET /api/shell/panes/<n>/messages?limit=40`.

From **WSL**, `127.0.0.1` is often the WSL VM — the host API may be firewalled;
use `python -m api.chat_cli` / `api.panes_cli` on the Windows host instead.

## Related

- Handle recognition (always-on rule): `.cuttle_global/rules/01-chat-handles.md`
- Agent ops CLI pattern: `.cuttle_global/docs/agent-ops-cli.md` / `.cuttle_global/rules/02-agent-ops-cli.md`

## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell, set
the path and use the Windows interpreter; for example:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.chat_cli get CH-000430-99 --json
```

Use PowerShell backticks for multiline continuation and single-quoted JSON
arguments; POSIX examples use backslashes for continuation.
