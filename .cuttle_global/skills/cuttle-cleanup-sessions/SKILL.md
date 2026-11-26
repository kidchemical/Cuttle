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

From the Cuttle repo root:

```bash
# Preview
.venv/bin/python .cuttle/scripts/cleanup-idle-sessions.py --hours 24 --dry-run

# Delete (keeps starred)
.venv/bin/python .cuttle/scripts/cleanup-idle-sessions.py --hours 24

# Also delete starred idles
.venv/bin/python .cuttle/scripts/cleanup-idle-sessions.py --hours 24 --include-starred
```

Windows: same arguments with `.venv\Scripts\python.exe .cuttle\scripts\cleanup-idle-sessions.py`.

Report `matched`, `deleted`, and whether any **starred** sessions were skipped.

## What “unused” means

Idle = `COALESCE(last_message_time, last_activity, created_at)` older than `--hours`
(UTC, same clock SQLite `CURRENT_TIMESTAMP` uses).

Chat rows are soft-deleted only: `UPDATE chat_sessions SET is_active = 0` — same as
`DELETE /api/auth/sessions/<id>`. Messages stay in DB; history UI hides them.

Uploads are different: a real run also removes each deleted chat's upload folder
(`<home>/output/uploads/<session_id>/`), which cannot be undone. Pass
`--skip-uploads` to keep the files. `--dry-run` removes nothing.

DB: `<home>/db/cuttle_auth.db` — `<home>` is the per-user Cuttle home (`~/.local/share/cuttle`, `%LOCALAPPDATA%\Cuttle`, or `$CUTTLE_HOME`; `core.runtime_paths.cuttle_home()`).

Script: `.cuttle/scripts/cleanup-idle-sessions.py` (Cuttle repo).

## Defaults agents must follow

1. Run **`--dry-run` first** unless the user already gave an explicit threshold and said to delete now.
2. **Keep starred** unless the user says to include them (`--include-starred`).
3. Do **not** call Flask session DELETE in a loop when the script can run — direct DB soft-delete is fine and does not need a Flask restart.
4. Do **not** hard-delete rows or wipe `chat_messages` unless the user explicitly asks for a destructive purge.
5. Never kill Flask (`kill`/`pkill`/`taskkill`) as part of this cleanup.

## Slash command and action (Cuttle repo chats)

The script, the command and the action belong to the Cuttle repo's own `.cuttle/`.
In a chat on another project, run the script from the Cuttle checkout as above.

- `/cleanup-sessions` → `.cuttle/commands/cleanup-sessions.md` (same script).
- Click-to-run: the allowlisted action `sessions.cleanup-idle`
  (`.cuttle/actions/sessions-cleanup-idle.yaml`) with params `hours`, `dry_run`
  and `include_starred`. Do not invent another action id.
