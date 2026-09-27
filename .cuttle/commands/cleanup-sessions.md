---
name: cleanup-sessions
title: cleanup-sessions
description: Soft-delete chat sessions idle longer than N hours (default 24; keeps starred)
execute: prompt
---

# Cleanup idle chat sessions

Follow the project skill **cuttle-cleanup-sessions**
([`.cursor/skills/cuttle-cleanup-sessions/SKILL.md`](file:////path/to/Cuttle/.cursor/skills/cuttle-cleanup-sessions/SKILL.md)).

## Do this

1. Parse hours from the user message if present (default **24**).
2. Preview:

```powershell
.venv\Scripts\python.exe .cuttle\scripts\cleanup-idle-sessions.py --hours 24 --dry-run
```

3. Unless the user already said to delete now, show the dry-run summary, then delete with the same `--hours` (omit `--dry-run`). Keep starred unless they ask otherwise (`--include-starred`).
4. Reply with counts: matched / deleted / starred skipped.

Do not restart Flask. Do not hard-delete DB rows.
