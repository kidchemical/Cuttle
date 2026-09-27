# Session Kinds and Per-Channel Pipeline Routing

> **Graphs removed (2026-09-26).** Per-channel pipeline routing is unused. Chat uses slash agents + the router. The rest of this file is historical.

Cuttle supports **session kinds** and **per-channel pipeline routing** so different channels or users can use different pipelines.

## Session kinds

- **main** – Owner/direct (can be mapped to a specific pipeline via routing).
- **web_user** – Authenticated web user (identity: `web_user_<user_id>`).
- **web_anon** – Anonymous web session (identity: `web_anon_<session_id>`).
- **discord_guild** – Discord server message (routing_key: `discord_guild_<guild_id>`).
- **discord_dm** – Discord DM (routing_key: `discord_dm_<user_id>`).

## Pipeline routing

Stored in `settings.json` under `pipeline_routing`:

```json
{
  "pipeline_routing": {
    "main": "OOBE_Welcome",
    "discord_guild_123456789": "Support_Pipeline",
    "web_user_42": "Personal_Pipeline"
  }
}
```

- **routing_key** → **pipeline_name**: When a message comes in with that routing_key (or session_kind), Cuttle uses that pipeline if it is running and has the matching trigger. If not set or pipeline not running, the first running pipeline with the trigger is used.

## API

- **GET /api/settings/pipeline-routing** – Get full routing map.
- **POST /api/settings/pipeline-routing** – Set or clear a route. Body: `{ "routing_key": "discord_guild_123", "pipeline_name": "Support_Pipeline" }`. Omit or null `pipeline_name` to clear.

## Example

1. Run two pipelines: "OOBE_Welcome" (trigger-webchat) and "Support_Pipeline" (trigger-webchat).
2. Set `pipeline_routing`: `{ "web_user_1": "Support_Pipeline" }`.
3. User 1 in Web Chat uses Support_Pipeline; other web users use the first running pipeline (e.g. OOBE_Welcome).
