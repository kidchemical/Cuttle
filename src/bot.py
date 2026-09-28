"""Compatibility shim for tests that import from ``bot``.

Does not load the retired Discord gateway.
"""
import os

try:
    OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
except ValueError:
    OWNER_ID = 0


def is_owner(message) -> bool:
    """Check if message author id matches OWNER_ID."""
    return getattr(message.author, "id", None) == OWNER_ID
