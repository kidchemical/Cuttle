---
name: scaffold-cuttle
title: Scaffold .cuttle/
description: Ensure the Cuttle repo has the standard .cuttle/ tree (idempotent)
execute: shell
run_posix: PYTHONPATH=src .venv/bin/python -m managers.cuttle_scaffold .
run_windows: set "PYTHONPATH=src" && .venv\Scripts\python.exe -m managers.cuttle_scaffold .
---

# Scaffold `.cuttle/` for this repo

Runs `managers.cuttle_scaffold` against the Cuttle repo root (idempotent; never
overwrites existing files). Prefer this when the tree is missing pieces.

For another project (agent / manual), from the Cuttle repo root:

```bash
PYTHONPATH=src .venv/bin/python -m managers.cuttle_scaffold "/path/to/project" --name "Display Name"
# Windows: $env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m managers.cuttle_scaffold "E:\path\to\project" --name "Display Name"
```

Registration via `POST /api/projects` already scaffolds automatically.
