"""Agent-facing chat history CLI (`python -m api.chat_cli`).

Wraps AuthDatabase / ``resolve_chat_handle_message`` so agents never hand-roll
SQL against ``cuttle_auth.db``. See ``.cuttle/docs/chat-history.md``.
"""

from api.chat_cli.cli import main

__all__ = ["main"]
