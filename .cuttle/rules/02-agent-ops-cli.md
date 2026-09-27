# Agent ops CLIs

When you need Cuttle-owned data or platform verbs (chat transcripts, workers, brain,
…), prefer **`python -m api.<module> <verb> …`** over hand-rolled SQL, ad-hoc curl, or
throwaway scripts.

- Chat handles / history → `python -m api.chat_cli` (see `chat-history.md`)
- Tasks widgets → `python -m api.widgets_cli` (see `widgets.md`; in-reply tags still preferred)
- Discord reads → `python -m api.discord_cli` (see `discord.md`; posts stay on `discord.post`)
- Gitea issues → `python -m api.gitea` (see `gitea.md`)
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
