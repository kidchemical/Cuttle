"""Optional Discord **agent operations** (REST read/post). Not an inbound chat surface.

Public action id remains ``discord.post``. CLI remains ``python -m api.discord_cli``.
"""

from api.discord_ops.post import execute_discord_post, resolve_post_channel_id
from api.discord_ops.token import load_discord_bot_token

__all__ = [
    "execute_discord_post",
    "load_discord_bot_token",
    "resolve_post_channel_id",
]
