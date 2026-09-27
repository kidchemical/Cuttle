"""
Compatibility shim for tests that import from 'bot'.
"""
import os

try:
    from bots.discord_bot import OWNER_ID

    def is_owner(message) -> bool:
        """Check if message author is the bot owner."""
        return getattr(message.author, "id", None) == OWNER_ID

except ImportError:
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))

    def is_owner(message) -> bool:
        return getattr(message.author, "id", None) == OWNER_ID
