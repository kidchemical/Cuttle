---
name: scaffold-cuttle
title: Scaffold .cuttle/
description: Ensure a project root has the standard .cuttle/ tree (idempotent)
execute: shell
run: ..\.venv\Scripts\python.exe -m managers.cuttle_scaffold .
workdir: src
---

# Scaffold `.cuttle/` for the chat project cwd

Runs `managers.cuttle_scaffold` against the current project working directory
(idempotent). Prefer this when a registered project is missing `.cuttle/`.

For an explicit path (agent / manual), from `/path/to/Cuttle/src`:

```text
..\.venv\Scripts\python.exe -m managers.cuttle_scaffold "E:\path\to\project" --name "Display Name"
```

Registration via `POST /api/projects` already scaffolds automatically.
