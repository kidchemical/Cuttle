"""Starred sticky slash agents (e.g. /cursor) for Cuttle chat.

The palette star is stored in settings.json so new chats (and any future
surface adapter using the same ingress) share the same default.
"""

from __future__ import annotations

from typing import Any, List, Optional

# Must match SLASH_COMMANDS with stickySession in chat_page.js (trailing space).
# Harness agents append via sticky_prefixes_from_harness() at normalize time.
_LEGACY_STICKY_PREFIXES = (
    "/cursor ",
    "/claude ",
    "/hermes ",
    "/codex ",
    "/muse ",
    "/opencode ",
)


def _all_sticky_prefixes() -> tuple:
    merged = list(_LEGACY_STICKY_PREFIXES)
    try:
        from api.agent_harness.catalog import sticky_prefixes_from_harness

        for p in sticky_prefixes_from_harness():
            if p not in merged:
                merged.append(p)
    except Exception:
        pass
    return tuple(merged)


# Public alias used throughout Cuttle (tuple so existing `in` checks keep working).
ALLOWED_STICKY_PREFIXES = _all_sticky_prefixes()


def refresh_sticky_prefixes() -> tuple:
    """Rebuild ALLOWED_STICKY_PREFIXES after harness catalog reload (tests)."""
    global ALLOWED_STICKY_PREFIXES
    ALLOWED_STICKY_PREFIXES = _all_sticky_prefixes()
    return ALLOWED_STICKY_PREFIXES
SETTINGS_KEY = "starred_slash_commands"


def normalize_sticky_prefix(raw: Any) -> Optional[str]:
    s = str(raw or "").strip()
    if not s:
        return None
    if not s.startswith("/"):
        s = "/" + s
    if not s.endswith(" "):
        s += " "
    low = s.lower()
    if low in ("/cursor-cli ", "/cursor-cli"):
        return "/cursor "
    for allowed in ALLOWED_STICKY_PREFIXES:
        if low == allowed.lower():
            return allowed
    return None


def sticky_prefix_from_text(text: str) -> Optional[str]:
    """Return the sticky prefix if this message already starts with one."""
    low = (text or "").strip().lower()
    if not low:
        return None
    if low.startswith("/cursor-cli"):
        return "/cursor "
    for allowed in ALLOWED_STICKY_PREFIXES:
        token = allowed.strip().lower()
        if low == token or low.startswith(token + " ") or low.startswith(allowed.lower()):
            return allowed
    return None


def get_starred_prefixes() -> List[str]:
    try:
        from managers.settings_manager import get_settings_manager

        raw = get_settings_manager().get_setting(SETTINGS_KEY) or []
    except Exception:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        n = normalize_sticky_prefix(item)
        if n and n not in out:
            out.append(n)
    return out[:1]


def set_starred_prefixes(prefixes: Any) -> List[str]:
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    if not isinstance(prefixes, list):
        prefixes = []
    normalized: List[str] = []
    for item in prefixes:
        n = normalize_sticky_prefix(item)
        if n and n not in normalized:
            normalized.append(n)
    normalized = normalized[:1]
    from managers.settings_manager import get_settings_manager

    get_settings_manager().set_setting(SETTINGS_KEY, normalized)
    return normalized


def _recent_messages(db_sid: Optional[int]) -> List[dict]:
    if db_sid is None:
        return []
    try:
        from api.auth_db import get_auth_db

        messages = get_auth_db().get_messages(int(db_sid), limit=40)
    except Exception:
        return []
    return messages if isinstance(messages, list) else []


def infer_session_sticky_prefix(db_sid: Optional[int]) -> Optional[str]:
    for msg in reversed(_recent_messages(db_sid)):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        found = sticky_prefix_from_text(str(msg.get("content") or ""))
        if found:
            return found
    return None


def session_has_user_turns(db_sid: Optional[int]) -> bool:
    """True when this chat already has a user message (i.e. not a fresh chat)."""
    return any(
        isinstance(m, dict) and m.get("role") == "user" for m in _recent_messages(db_sid)
    )


# UI control payloads (action-form clicks, buttons) are not prompts — prefixing
# them with an agent slash would route a click into an agent run.
_CONTROL_PAYLOAD_PREFIXES = ("[button:", "[action-form:", "[form:")

# A client that removed the agent badge says so explicitly; anything else keeps
# the star fallback for callers that never had a badge to begin with.
_NO_AGENT_KEYS = ("sticky_agent", "stickyAgent", "agent")
_NO_AGENT_TOKENS = frozenset(
    {"none", "off", "no", "false", "router", "no-agent", "no_agent"}
)

# Everything except /hermes needs a cloud CLI, which Local mode refuses.
_LOCAL_SAFE_PREFIXES = ("/hermes ",)


def is_no_agent_request(data: Any) -> bool:
    """True when the request asked to run without a sticky/starred agent."""
    if not isinstance(data, dict):
        return False
    for key in _NO_AGENT_KEYS:
        raw = data.get(key)
        if raw is None:
            continue
        if raw is False:
            return True
        if str(raw).strip().lower() in _NO_AGENT_TOKENS:
            return True
    return False


def resolve_sticky_prefix(
    message: str,
    db_sid: Optional[int] = None,
    *,
    star_on_new_session_only: bool = False,
) -> Optional[str]:
    """The sticky prefix this message should run under, or None.

    Session history wins over the global star so a chat that already picked
    Cursor stays on Cursor even if the star later changes. ``star_on_new_session_only``
    mirrors the chat page, where the star seeds new chats but never takes over a
    conversation that has been running without an agent.
    """
    text = (message or "").strip()
    if not text or text.startswith("/"):
        return None
    if any(text.startswith(p) for p in _CONTROL_PAYLOAD_PREFIXES):
        return None
    prefix = infer_session_sticky_prefix(db_sid)
    if prefix:
        return prefix
    if star_on_new_session_only and session_has_user_turns(db_sid):
        return None
    starred = get_starred_prefixes()
    return starred[0] if starred else None


def apply_default_sticky_prefix(
    message: str,
    db_sid: Optional[int] = None,
    *,
    allow_cloud_cli: bool = True,
    star_on_new_session_only: bool = False,
    no_agent: bool = False,
) -> str:
    """Prepend the session sticky or starred default onto a plain message.

    Leaves explicit slash commands alone (``/help``, ``/cursor …``).
    With ``allow_cloud_cli`` false (Local-mode chats) only local-capable
    prefixes are applied, so a plain message never turns into a blocked
    cloud-CLI command. ``no_agent`` is the user removing the agent badge: the
    star and the session's own history both lose, and the turn reaches the
    agent router.
    """
    text = (message or "").strip()
    if not text:
        return text
    if no_agent:
        return text
    if sticky_prefix_from_text(text):
        return text
    prefix = resolve_sticky_prefix(
        text, db_sid, star_on_new_session_only=star_on_new_session_only
    )
    if not prefix:
        return text
    if not allow_cloud_cli and prefix not in _LOCAL_SAFE_PREFIXES:
        return text
    return prefix + text
