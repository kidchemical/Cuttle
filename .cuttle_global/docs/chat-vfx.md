# Chat visual effects

Agents publish transient effects through `api.chat_vfx`. No experimental flag is
required. Effects target a numeric chat session or CH handle; confetti draws inside
the open chat pane and honors reduced-motion, animation, and notification preferences.

From the Cuttle checkout, with the project venv:

```bash
PYTHONPATH=src .venv/bin/python -m api.chat_vfx confetti --session CH-000927 --count 120
PYTHONPATH=src .venv/bin/python -m api.chat_vfx toast --session CH-000927 "Done" --variant success
PYTHONPATH=src .venv/bin/python -m api.chat_vfx pending --session CH-000927
```

Library: `spawn_confetti(session_id, count=120)`, `toast(session_id, message,
variant='info')`, and `pending(session_id, after=0)`. CLI output is JSON; errors
return nonzero. Local CLI/library callers are trusted agents, like other local
Cuttle ops. They do not receive HTTP authorization checks.

HTTP: GET/POST `/api/chat-vfx/<session_id>`. Reads require ownership of that chat;
writes additionally require an owner account. POST accepts
`{"kind":"confetti","count":120}` or
`{"kind":"toast","message":"Done","variant":"success"}`.
GET accepts `?after=<sequence>` and does not consume events.

Publication returns an event ID and **queued**, not a rendering acknowledgment.
Clients poll every two seconds. Events expire after 30 seconds, so closed chats
will miss them. Each browser has an independent cursor; refresh can replay an
unexpired event. No promise of exactly-once display across refresh or devices.
Expired events are removed on reads/writes; at most 1,000 events are retained per
session. The private SQLite store defaults to `<home>/db/chat_vfx.db`;
`CUTTLE_CHAT_VFX_DB` overrides it for isolated tests.

Achievements retains its durable unlock/ack flow and shares the generic confetti
renderer. These preview effects never grant an achievement. Adding sounds or
other effects later requires an explicit validated type and renderer.

## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell, set
the path and use the Windows interpreter; for example:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.chat_vfx pending --session CH-000927
```

Use PowerShell backticks for multiline continuation and single-quoted JSON
arguments; POSIX examples use backslashes for continuation.
