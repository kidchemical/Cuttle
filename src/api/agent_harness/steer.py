"""Mid-turn steering: push a user follow-up into the agent turn that is already running.

Codex (``codex app-server`` → ``turn/steer``), Muse Code (``muse serve`` →
``turn/steer``) and Claude Code (``claude -p --input-format stream-json`` with
stdin left open) accept extra input while a turn is live. Their turn runners
register a send callback here once the harness has the turn;
``POST /api/chat-steer`` looks the chat up and forwards the text.

Anything that cannot be steered (no live registration, a different slash
command, the harness rejecting a turn that just finished) returns
``steered=False`` so the client falls back to its follow-up queue.

Settings: ``agent_steer`` → ``{"codex": true, "muse": true, "claude": true}``
(all default on). Turning an agent off also makes its adapter use the one-shot
path (``exec`` / plain ``claude -p``).
``CUTTLE_AGENT_STEER=0`` turns steering off for every agent.
"""

from __future__ import annotations

import concurrent.futures
import itertools
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

STEERABLE_AGENTS = ("codex", "muse", "claude")

# The send callback receives the steer text and returns a Future resolving to
# ``(ok, error_message_or_None)``. It is called from a Flask worker thread.
SteerSend = Callable[[str], "concurrent.futures.Future"]


@dataclass
class _Entry:
    agent_id: str
    token: int
    send: SteerSend


_lock = threading.Lock()
_active: Dict[str, _Entry] = {}
_tokens = itertools.count(1)


def _key(chat_session_id: Any) -> Optional[str]:
    if chat_session_id is None or str(chat_session_id).strip() == "":
        return None
    try:
        from api.cuttle_ui_capabilities import numeric_chat_session_id

        num = numeric_chat_session_id(chat_session_id)
    except Exception:
        num = None
    return str(num) if num is not None else str(chat_session_id).strip()


def steer_env_disabled() -> bool:
    """``CUTTLE_AGENT_STEER=0`` overrides every per-agent setting."""
    return (os.getenv("CUTTLE_AGENT_STEER") or "").strip().lower() in ("0", "false", "off", "no")


def steer_enabled(agent_id: str) -> bool:
    agent = (agent_id or "").strip().lower()
    if agent not in STEERABLE_AGENTS:
        return False
    if steer_env_disabled():
        return False
    try:
        from managers.settings_manager import get_settings_manager

        cfg = get_settings_manager().get_setting("agent_steer", None)
    except Exception:
        cfg = None
    if isinstance(cfg, dict) and agent in cfg:
        return bool(cfg.get(agent))
    return True


def steer_settings() -> Dict[str, bool]:
    """Saved per-agent switches (env kill switch not applied), defaults on."""
    try:
        from managers.settings_manager import get_settings_manager

        cfg = get_settings_manager().get_setting("agent_steer", None)
    except Exception:
        cfg = None
    cfg = cfg if isinstance(cfg, dict) else {}
    return {agent: bool(cfg.get(agent, True)) for agent in STEERABLE_AGENTS}


def set_steer_enabled(agent_id: str, enabled: bool) -> bool:
    """Persist one agent's switch; False for agents that cannot steer."""
    agent = (agent_id or "").strip().lower()
    if agent not in STEERABLE_AGENTS:
        return False
    from managers.settings_manager import get_settings_manager

    def _apply(current: Any) -> Dict[str, Any]:
        out = dict(current) if isinstance(current, dict) else {}
        out[agent] = bool(enabled)
        return out

    return get_settings_manager().update_setting("agent_steer", _apply)


def register(chat_session_id: Any, agent_id: str, send: SteerSend) -> Optional[int]:
    """Mark ``chat_session_id`` as having a steerable live turn. Returns a token."""
    key = _key(chat_session_id)
    if key is None:
        return None
    token = next(_tokens)
    with _lock:
        _active[key] = _Entry(agent_id=agent_id, token=token, send=send)
    return token


def unregister(chat_session_id: Any, token: Optional[int]) -> None:
    """Drop the registration if it still belongs to ``token``."""
    key = _key(chat_session_id)
    if key is None or token is None:
        return
    with _lock:
        entry = _active.get(key)
        if entry is not None and entry.token == token:
            del _active[key]


def active_agent(chat_session_id: Any) -> Optional[str]:
    key = _key(chat_session_id)
    if key is None:
        return None
    with _lock:
        entry = _active.get(key)
        return entry.agent_id if entry else None


_SLASH_RE = re.compile(r"^/([A-Za-z][\w-]*)\b\s*", re.S)
_AGENT_SLASH_ALIASES = {
    "codex": ("codex",),
    "muse": ("muse",),
    "claude": ("claude",),
}


def steer_text_for(message: str, agent_id: str) -> Optional[str]:
    """Strip the running agent's sticky prefix; None when the text must not be steered.

    Other slash commands (``/cursor …``, ``/codex model …``, ``/restart``) are
    routing or control input, not conversation, so they stay on the queue path.
    """
    text = (message or "").strip()
    if not text:
        return None
    match = _SLASH_RE.match(text)
    if not match:
        return text
    if match.group(1).lower() not in _AGENT_SLASH_ALIASES.get(agent_id, (agent_id,)):
        return None
    body = text[match.end():].strip()
    if not body or _SLASH_RE.match(body):
        return None
    if re.match(r"^(models?|efforts?|usage|reset|new|clear)\b", body, re.I):
        return None
    return body


def steer(chat_session_id: Any, message: str, *, timeout: float = 10.0) -> Dict[str, Any]:
    """Forward ``message`` to the live turn for this chat."""
    key = _key(chat_session_id)
    if key is None:
        return {"steered": False, "reason": "no_session"}
    with _lock:
        entry = _active.get(key)
    if entry is None:
        return {"steered": False, "reason": "no_steerable_run"}
    text = steer_text_for(message, entry.agent_id)
    if text is None:
        return {"steered": False, "reason": "not_steerable_text", "agent": entry.agent_id}
    try:
        fut = entry.send(text)
        ok, err = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return {"steered": False, "reason": "timeout", "agent": entry.agent_id}
    except Exception as exc:
        return {"steered": False, "reason": "error", "error": str(exc), "agent": entry.agent_id}
    if not ok:
        return {
            "steered": False,
            "reason": "rejected",
            "error": err,
            "agent": entry.agent_id,
        }
    return {"steered": True, "agent": entry.agent_id, "text": text}
