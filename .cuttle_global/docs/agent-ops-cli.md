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
| Chat handles / transcripts | `python -m api.chat_cli` | `chat-history.md` |
| Tasks widgets | `python -m api.widgets_cli` | `widgets.md` |
| Cuttle QA / promo accounts | `python -m api.fixture_accounts` | Cuttle project `.cuttle/docs/fixture-accounts.md` |
| Discord reads | `python -m api.discord_cli` | `discord.md` (posts stay on `discord.post`) |
| Gitea issues | `python -m api.gitea` | `gitea.md` |
| Live split panes | `python -m api.panes_cli` | `chat-history.md` |
| Device mesh / workers | `python -m api.device_workers.cli` | `cuttle-workers.md` |
| Context Compiler | `python -m api.cuttle_brain` | Brain / ADDING_AN_AGENT |
| Sub-agent child chats | `python -m api.subagents` | `subagents.md` |
| Job watch bars | `python -m api.job_watch` | `action-forms.md` |
| Dashboards / Model Benchmarks | `python -m api.dashboards` | `dashboards.md` |
| Jev judgments | `python -m api.jev` | `jev.md` |
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
.venv\Scripts\python.exe -m api.chat_cli get CH-000430-99 --json
```

Put flags after the verb. Working directory / `PYTHONPATH` should resolve the `api`
package (repo root with `src` on path, or `cwd=src` as Flask uses).
