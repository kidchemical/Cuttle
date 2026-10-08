# Agent ops CLIs

When you need Cuttle-owned data or platform verbs (chat transcripts, workers, brain,
…), prefer **`python -m api.<module> <verb> …`** over hand-rolled SQL, ad-hoc curl, or
throwaway scripts.

- Chat handles / history → `python -m api.chat_cli` (see `chat-history.md`)
- Tasks gizmos → `python -m api.gizmos tasks` (see `gizmos.md`; create during planning, patch during work, no Tasks markdown tags)
- Gizmos (shell-docked usage meters, experimental) → `python -m api.gizmos` (see `gizmos.md`)
- Discord reads → `python -m api.discord_cli` (see `discord.md`; posts stay on `discord.post`)
- Forge issues → the active project's enabled integration runbook/CLI
- Live panes → `python -m api.panes_cli` (see `chat-history.md`)
- Workers mesh → `python -m api.device_workers.cli` (see `cuttle-workers.md`)
- Context compile → `python -m api.cuttle_brain`
- Sub-agent child chats → `python -m api.subagents` (see `subagents.md`)
- Dashboards / Model Benchmarks → `python -m api.dashboards` (see `dashboards.md`)
- Jev judgments → `python -m api.jev` (see `jev.md`)
- Pattern / how to extend → `agent-ops-cli.md`

This is the **agent toolkit**, not a product `cuttle` shell for humans. When adding
agent-facing features, ship library + `python -m` verbs + update the runbook — do not
teach raw DB recipes as the primary path. Do **not** add a Cuttle-hosted MCP tool
server; guest harnesses own MCP, Cuttle ops stay on `python -m api.*`.
