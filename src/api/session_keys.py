"""Canonical aliases for a Cuttle chat session id."""

from __future__ import annotations

from typing import Any, List


def bare_chat_session_id(session_id: Any) -> str:
    """Canonical numeric id when the value is a Cuttle handle, else the string key.

    ``"CH-000478"``, ``"db_session_478"``, and ``"478"`` all become ``"478"``.
    Message refs (``CH-000478-9``) resolve to the session id only.
    Non-numeric ids (synthetic worker sessions, etc.) are returned unchanged.
    """
    if session_id is None:
        return ""
    k = str(session_id).strip()
    if not k:
        return ""
    if k.startswith("db_session_"):
        k = k[len("db_session_") :]
    if len(k) > 3 and k[:3].upper() == "CH-":
        rest = k[3:]
        if rest.isdigit():
            return str(int(rest))
        if "-" in rest:
            session_part, _, msg_part = rest.partition("-")
            if session_part.isdigit() and msg_part.isdigit() and int(msg_part) >= 1:
                return str(int(session_part))
        return k
    try:
        return str(int(k))
    except (TypeError, ValueError):
        return k


def chat_session_keys(session_id) -> List[str]:
    """All aliases a session may be referred to by ("12", "db_session_12", "CH-000012").

    Message refs (``CH-000012-9``) resolve to the same session aliases as the
    bare chat handle.
    """
    if session_id is None:
        return []
    s = str(session_id).strip()
    if not s:
        return []
    keys = [s]
    bare = s
    if s.startswith("db_session_"):
        bare = s[len("db_session_") :]
        if bare:
            keys.append(bare)
    elif len(s) > 3 and s[:3].upper() == "CH-":
        rest = s[3:]
        # CH-000012 or CH-000012-9 (1-based bubble index)
        if rest.isdigit():
            bare = str(int(rest))
            keys.append(bare)
        elif "-" in rest:
            session_part, _, msg_part = rest.partition("-")
            if session_part.isdigit() and msg_part.isdigit() and int(msg_part) >= 1:
                bare = str(int(session_part))
                keys.append(bare)
                keys.append(f"CH-{int(bare):06d}")
            else:
                bare = s
        else:
            bare = s
    else:
        try:
            bare = str(int(s))
            if bare != s:
                keys.append(bare)
        except (TypeError, ValueError):
            bare = s
    if bare.isdigit():
        keys.append(f"db_session_{bare}")
        keys.append(f"CH-{int(bare):06d}")
    out: List[str] = []
    seen = set()
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out
