"""Durable, atomic dispatch claims. A claim is not exactly-once external effects.

Ambiguous failures retain the claim: a fresh confirmation is required to retry.
Policy comes from the resolved allowlisted recipe, never from a card or token.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import time


def policy(raw, params) -> str:
    if raw is None:
        return "once"
    if isinstance(raw, str):
        value = raw
    elif isinstance(raw, dict) and not set(raw) - {"default", "modes"}:
        modes = raw.get("modes", {})
        if not isinstance(modes, dict) or any(v not in ("once", "reusable") for v in modes.values()):
            raise ValueError("invalid replay modes")
        default = raw.get("default", "once")
        if default not in ("once", "reusable"):
            raise ValueError("invalid replay default")
        value = modes.get(str(params.get("mode") or ""), default)
    else:
        raise ValueError("invalid action replay policy")
    if value not in ("once", "reusable"):
        raise ValueError("replay policy must be once or reusable")
    return value


def key(receipt_id, action, project, session):
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    session = numeric_chat_session_id(session) or str(session or "")
    return hashlib.sha256(json.dumps([receipt_id, action, project, session], separators=(",", ":")).encode()).hexdigest()


def claim(receipt_key: str, expires_at: int = 0) -> bool:
    from api.auth_db import get_auth_db

    with closing(get_auth_db()._get_connection()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS action_replay_receipts (receipt_key TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM action_replay_receipts WHERE expires_at > 0 AND expires_at < ?", (int(time.time()),))
        inserted = conn.execute("INSERT OR IGNORE INTO action_replay_receipts VALUES (?, ?)", (receipt_key, expires_at)).rowcount
        conn.commit()
        return bool(inserted)


def release_rejected(receipt_key: str) -> None:
    """Only for a native controller's explicit rejection before dispatch."""
    from api.auth_db import get_auth_db

    with closing(get_auth_db()._get_connection()) as conn:
        conn.execute("DELETE FROM action_replay_receipts WHERE receipt_key = ?", (receipt_key,))
        conn.commit()


def duplicate_result():
    return {"success": False, "already_used": True, "type": "project_action",
            "response": "This action was already dispatched or cancelled. Request a fresh confirmation to retry."}
