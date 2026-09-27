---
name: remote-agent-routing
description: >-
  Where Cuttle decides between guest agent CLIs, local/remote LLMs, and
  fallbacks. Use when debugging why a message did not route as expected.
---

# Remote agent routing

## Primary hook

Heavy lifting lives in **`web_chat_api.py`**, in `_execute_remote_agent_tool()`
and surrounding helpers. That code:

- Resolves **project** / working directory (e.g. mapping `pc_bot` to the real repo root).
- Chooses a **guest CLI** vs **LLM fallback** based on owner session, keywords, and configuration.
- Handles timeouts, errors, and streaming status back to the UI where applicable.

When the user says “remote agent always uses X” or “never invokes Cursor,” start
by reading that function and the slash-agent / router path.

## Chat / Discord context

- Web chat: `/api/chat` plus starred sticky prefixes and the agent router.
- Discord: `bot_mcp.py` → `/api/pipeline-trigger-discord` → `process_message_with_bot`.
- **Sandbox / non-owner** sessions may restrict tools; see `settings_manager`.

## Related layers

| Concern | Where to look |
|--------|----------------|
| Slash agents / harness | `src/api/agent_harness/` |
| Router | `src/api/agent_router/` |
| Web chat payload / session id | `web_chat_api.py` routes under `/api/chat` |
| Discord | `bot_mcp.py` → API |

## Debugging tips

1. Confirm the turn actually hit a slash agent or the router (star, sticky chip, or clean session).
2. Log or trace `_execute_remote_agent_tool` arguments: prompt length, project key, session flags.

Do not duplicate routing logic in new code paths; extend the existing helper so behavior stays consistent across web and Discord.
