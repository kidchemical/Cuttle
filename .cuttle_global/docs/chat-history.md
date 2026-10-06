# Cuttle chat history

Transcripts live in a **gitignored SQLite DB**, not in the working tree. Empty
`rg`/`grep` over the repo is **not** evidence that Cuttle has no chats — never
answer “no traces of Cuttle chats found” on that basis alone.

Open this file before reading or summarizing chat history.
Prefer `.cuttle_global/personal/docs/chat-history.md` when it exists (install-local DB path).

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

| Verb | Use |
|---|---|
| `get CH-…` | Session summary, or one bubble when `CH-…-N` |
| `session CH-…` | List bubbles with UI share indices |
| `search "…"` | Title/body search (pass `--from-chat` on multi-user DBs) |
| `parse CH-…` | Parse handle only (no DB) |

Exit codes: `0` ok, `2` not found / bad handle, `3` other error.

Library (same semantics): `resolve_chat_handle_message`, 
`AuthDatabase.get_message_by_share_index`. Design pattern: `.cuttle_global/docs/agent-ops-cli.md`.

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
| Default path | `{CuttleInstall}/src/data/db/cuttle_auth.db` (resolve via runtime / `api.auth_db.DB_PATH`) |

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
