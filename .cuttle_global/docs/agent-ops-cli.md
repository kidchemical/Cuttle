# Agent ops CLIs (global)

Cuttle’s product UI is the daemon + web/Electron chat. Separately, agents
need **stable shell verbs** for local ops (transcripts, workers, brain, Discord REST, …).

## Pattern

1. **Library first** — Python helpers in `src/api/` own semantics (share indices, auth DB).
2. **`python -m api.<module>`** — thin argparse front for agents (JSON out, clear exit codes).
3. **Runbook** — `.cuttle_global/docs/<topic>.md` teaches the CLI, not raw SQL / ad-hoc curl.
4. **Optional** — `.cuttle_global/actions/*.yaml` clickable forms (same verbs). Do **not** add a Cuttle-hosted MCP tool server for agents.

This is **not** a user-facing `cuttle.exe` product shell (still deferred). It is the
agent toolkit.

## Existing modules

| Domain | Module | Doc |
|---|---|---|
| Agent activity / database policies | `python -m api.agent_events` / `python -m api.storage` | `agent-events.md` |
| Chat handles / transcripts | `python -m api.chat_cli` | `chat-history.md` |
| Chat visual effects | `python -m api.chat_vfx` | `chat-vfx.md` |
| Experimental flags | `python -m api.experimental` (list, get, set, reset) | Cuttle `.cuttle/docs/experimental-features.md` |
| Tasks gizmos | `python -m api.gizmos tasks` | `gizmos.md` |
| Cuttle QA / promo accounts | `python -m api.fixture_accounts` | Cuttle project `.cuttle/docs/fixture-accounts.md` |
| Discord reads | `python -m api.discord_cli` | `discord.md` (posts stay on `discord.post`) |
| Live split panes | `python -m api.panes_cli` | `chat-history.md` |
| Device mesh / workers | `python -m api.device_workers.cli` | `cuttle-workers.md` |
| Context Compiler | `python -m api.cuttle_brain` | Brain / ADDING_AN_AGENT |
| Sub-agent child chats | `python -m api.subagents` | `subagents.md` |
| Job watch bars | `python -m api.job_watch` | `action-forms.md` |
| Dashboards / Model Benchmarks | `python -m api.dashboards` | `dashboards.md` |
| Jev judgments | `python -m api.jev` | `jev.md` |
| Shadow dev instance (implemented, Linux verified) | `PYTHONPATH=src .venv/bin/python -m api.dev_instance prepare --candidate .` then `PYTHONPATH=src .venv/bin/python -m api.dev_instance up --candidate . --port 0` (foreground; Ctrl+C stops only its owned child and deletes its snapshot; `prune` clears stale ones) | Cuttle project `docs/architecture/development-instance-safety.md` |
| Agent context fill / compact | `GET/POST /api/agent-context` | `agent-context.md` |

Cuttle-as-MCP-server (`run_cuttle_mcp.py`, Tools-page pack toggles, pipeline MCP toolsets for Ollama) is **retired**. Do not spawn it from Flask or Discord. New agent-facing capabilities go through this table.

## When adding a new agent-facing capability

- Ship or reuse a library API.
- Add `python -m` verbs (prefer `--json`).
- Point the matching `.cuttle_global/docs/` runbook + a short `.cuttle_global/rules/` line at the CLI.
- Do **not** make hand-rolled SQL or one-off `_tmp_*.py` the documented primary path.

## Invocation

From the Cuttle repo root with the **project venv** (not bare `python`):

```bash
PYTHONPATH=src .venv/bin/python -m api.chat_cli get CH-000430-99 --json
```

Put flags after the verb. `PYTHONPATH=src` resolves the package under `src/api`;
pytest configuration does not set the path for ordinary CLI invocations.

## Local planning and execution

For local coding use a configured llama.cpp backend or a guest CLI (`/hermes`,
`/cursor`, …). Cuttle-owned operations use `python -m api.*`, not a hosted MCP
server. Ollama remains an optional `/api/llm-request` backend; requests are
serialized per model. Do not rebuild retired pipeline-node tool loops around it.

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


## Projects registry

Use `PYTHONPATH=src .venv/bin/python -m api.projects_cli` from the Cuttle root:
`list`, `get <id>`, `check <absolute-path> …`, `register --name … --path …`,
`update <id> --json '{"paths":["/host/project","D:/project"]}'`, and
`remove <id> --confirm-name …`. Locations resolve in order on the Host, never the
viewing client. Removal preserves worktree files and chats. Details:
[Projects App](../../.cuttle/docs/projects-app.md).
