# Sandbox for Non-Owner Sessions

Cuttle supports an optional **sandbox** that restricts which pipelines and tools can run for non-owner sessions (e.g. Discord guilds, web anonymous, Telegram/Slack users).

## Config

Stored in `settings.json` under `sandbox`:

- **enabled** – When true, sandbox rules apply for session kinds in `restrict_for_session_kinds`.
- **restrict_for_session_kinds** – Session kinds that are sandboxed: `web_anon`, `discord_guild`, `discord_dm`, `telegram_user`, `slack_user`. Owner sessions (e.g. `user_context.is_owner`) are never sandboxed.
- **allowed_tools** – List of tool node types allowed in sandbox (e.g. `tool-screenshot`, `tool-process`, `llm`, `llm-prompt`). Other tool nodes are skipped.
- **denied_tools** – Optional list of tool node types that are **always** blocked in sandbox (even if listed in `allowed_tools`). Use for dangerous nodes you never want in public sessions.
- **denied_tool_prefixes** – Optional list of lowercase prefixes: if a tool node type starts with any prefix, it is blocked (e.g. `tool-mcp` blocks `tool-mcp-generic` without naming every variant).
- **allowed_pipelines** – List of pipeline names allowed for sandboxed sessions, or `["*"]` to allow any pipeline.

**Pipeline limits** (top-level `pipeline_limits` in `settings.json`):

- **max_tool_nodes_per_execution** – Maximum number of `tool-*` nodes that may run in a single pipeline execution (default 64). Prevents runaway graphs from looping tools indefinitely.

## API

- **GET /api/settings/sandbox** – Get sandbox config.
- **POST /api/settings/sandbox** – Update. Body may include `enabled`, `allowed_tools`, `denied_tools`, `denied_tool_prefixes`, `allowed_pipelines`, `restrict_for_session_kinds`.
- **GET /api/settings/pipeline-limits** – Get `pipeline_limits`.
- **POST /api/settings/pipeline-limits** – Update. Body: `{ "max_tool_nodes_per_execution": 64 }`.

## Behavior

- When sandbox is enabled and the session is in `restrict_for_session_kinds` and the user is not the owner:
  - Only pipelines in `allowed_pipelines` (or any if `["*"]`) can run.
  - For each `tool-*` node: **deny rules apply first** (explicit name or prefix), then the node must appear in `allowed_tools` or it is skipped.
  - Skips emit a streaming status line `[sandbox] Skipped …` and add an entry to `sandbox_denials` on the pipeline result when the client reads the response payload.
- If the number of tool nodes that would execute exceeds **max_tool_nodes_per_execution**, the run stops with an error and a `[limit]` status line.
- Owner is determined by `user_context.is_owner` (e.g. Discord owner ID). Web Chat authenticated users are not automatically owners; you can extend logic to treat specific user IDs as owner.
