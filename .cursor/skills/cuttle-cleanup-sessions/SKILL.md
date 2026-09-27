---
name: cuttle-cleanup-sessions
description: >-
  Soft-delete idle Cuttle web chat sessions from cuttle_auth.db (is_active=0),
  default keep starred, optional upload-dir cleanup. Use when the user asks to
  clean up / prune / delete old or unused chat sessions, chat history older than
  N hours/days, or run /cleanup-sessions.
---

# Cleanup idle Cuttle chat sessions

## Quick path (preferred)

From repo root `/path/to/Cuttle`:

```powershell
# Preview
.venv\Scripts\python.exe .cuttle\scripts\cleanup-idle-sessions.py --hours 24 --dry-run

# Delete (keeps starred)
.venv\Scripts\python.exe .cuttle\scripts\cleanup-idle-sessions.py --hours 24

# Also delete starred idles
.venv\Scripts\python.exe .cuttle\scripts\cleanup-idle-sessions.py --hours 24 --include-starred
```

Report `matched`, `deleted`, and whether any **starred** sessions were skipped.

## What “unused” means

Idle = `COALESCE(last_message_time, last_activity, created_at)` older than `--hours`
(UTC, same clock SQLite `CURRENT_TIMESTAMP` uses).

Soft-delete only: `UPDATE chat_sessions SET is_active = 0` — same as
`DELETE /api/auth/sessions/<id>`. Messages stay in DB; history UI hides them.

DB: [file:////path/to/Cuttle/src/api/data/cuttle_auth.db](file:////path/to/Cuttle/src/api/data/cuttle_auth.db)

Script: [file:////path/to/Cuttle/.cuttle/scripts/cleanup-idle-sessions.py](file:////path/to/Cuttle/.cuttle/scripts/cleanup-idle-sessions.py)

## Defaults agents must follow

1. Run **`--dry-run` first** unless the user already gave an explicit threshold and said to delete now.
2. **Keep starred** unless the user says to include them (`--include-starred`).
3. Do **not** call Flask session DELETE in a loop when the script can run — direct DB soft-delete is fine and does not need a Flask restart.
4. Do **not** hard-delete rows or wipe `chat_messages` unless the user explicitly asks for a destructive purge.
5. Never `taskkill` Flask as part of this cleanup.

## Slash command

`/cleanup-sessions` → `.cuttle/commands/cleanup-sessions.md` (same script).

## Optional confirm card

If the user wants a click-to-run control next time, emit a confirm that documents the
command (or add a `.cuttle/actions/` shell action wrapping the script). Do not invent
an action id that is not allowlisted.
