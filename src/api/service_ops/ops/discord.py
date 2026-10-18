"""Discord service op — the ``discord.post`` action type.

Thin adapter over the owner (``api.discord_ops.post``); the REST/token/allowlist
behavior stays there. Registering the type(s) here keeps ``project_actions``
free of service-specific branches.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

ACTION_TYPES: Tuple[str, ...] = ("discord.post", "discord_post", "discord")


class DiscordOp:
    id = "discord"
    label = "Discord"
    action_types = ACTION_TYPES
    description = "Confirmed Discord channel posts (REST; alias allowlist only)."

    def available(self, action: Dict[str, Any], project_path: Optional[str] = None) -> bool:
        from api.experimental.context_bundles import executable_available

        return bool(executable_available(action, project_path or action.get("project_path")))

    def execute(self, action: Dict[str, Any], params: Dict[str, Any], *,
                session_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.available(action, action.get("project_path")):
            return {"success": False, "error": "Feature action is unavailable."}
        from api.discord_ops.post import execute_discord_post

        return execute_discord_post(action, params)


def build_op() -> DiscordOp:
    return DiscordOp()
