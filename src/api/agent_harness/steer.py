"""Mid-turn steering: push a user follow-up into the agent turn that is already running.

Codex (``codex app-server`` → ``turn/steer``), Muse Code (``muse serve`` →
``turn/steer``), Claude Code (``claude -p --input-format stream-json`` with
stdin left open) and Antigravity (``agy acp`` → mid-turn ``session/prompt``)
accept extra input while a turn is live. Their turn runners
register a send callback here once the harness has the turn;
``POST /api/chat-steer`` looks the chat up and forwards the text.

A Claude run that outlived its turn on a background wait
(``api.agent_harness.background_wait``) stays registered here, so the chat is
idle yet steerable; ``api.chat_coordinator`` routes harness sends into it.

Anything that cannot be steered (no live registration, a different slash
command, the harness rejecting a turn that just finished) returns
``steered=False`` so the client falls back to its follow-up queue.

Settings: ``agent_steer`` → ``{"codex": true, "muse": true, "claude": true, "antigravity": true}``
(all default on). Turning an agent off also makes its adapter use the one-shot
path (``exec`` / plain ``claude -p`` / ``agy -p``).
``CUTTLE_AGENT_STEER=0`` turns steering off for every agent.
"""

from __future__ import annotations

import concurrent.futures
import inspect
import itertools
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

STEERABLE_AGENTS = ("codex", "muse", "claude", "antigravity")

# The send callback receives the steer text and this send's ``steer_id`` (so the
# runner's own echo matcher can report delivery of that exact send) and returns
# a Future resolving to ``(ok, error_message_or_None)``. A one-argument callback
# is still accepted. It is called from a Flask worker thread.
SteerSend = Callable[..., "concurrent.futures.Future"]


@dataclass
class _Entry:
    agent_id: str
    token: int
    send: SteerSend
    receipts: Any


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
    from api.agent_harness import steer_receipts

    token = next(_tokens)
    # Pin this run's identity now. A steer sent later must not pick up the turn
    # of whatever replaced this registration (Stop → re-send), and a receipt
    # whose turn was superseded must not be published or saved.
    try:
        from api.chat_turn_id import ambient

        turn_id = ambient(chat_session_id)
    except Exception:
        turn_id = ""
    try:
        from api import chat_delivery

        guard_turn = chat_delivery.current_turn(chat_session_id)
    except Exception:
        guard_turn = None
    receipts = steer_receipts.Receipts(
        steer_receipts.changed, many=steer_receipts.changed_many,
        session=key, turn_id=turn_id, guard_turn=guard_turn
    )
    with _lock:
        previous = _active.get(key)
        _active[key] = _Entry(agent_id=agent_id, token=token, send=send, receipts=receipts)
    if previous is not None:
        previous.receipts.finish()
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
        else:
            entry = None
    if entry is not None:
        entry.receipts.finish()


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


def steer(chat_session_id: Any, message: str, *, timeout: float = 10.0, turn_id: Optional[str] = None,
          expect_token: Optional[int] = None) -> Dict[str, Any]:
    """Forward ``message`` to the live turn for this chat.

    ``expect_token`` names the registration the caller means: a detached wait
    passes its own token, so a send never reaches the replacement CLI of a run
    that was swapped out while the caller was looking the wait up.
    """
    key = _key(chat_session_id)
    if key is None:
        return {"steered": False, "reason": "no_session"}
    with _lock:
        entry = _active.get(key)
    if entry is None:
        return {"steered": False, "reason": "no_steerable_run"}
    if expect_token is not None and entry.token != expect_token:
        return {"steered": False, "reason": "stale_registration", "agent": entry.agent_id}
    text = steer_text_for(message, entry.agent_id)
    if text is None:
        return {"steered": False, "reason": "not_steerable_text", "agent": entry.agent_id}
    # Register the receipt before the transport can echo the send, against the
    # turn pinned at registration — never the chat's ambient turn, which a
    # replacement run may already own.
    from api.agent_harness import steer_receipts

    steer_id = entry.receipts.begin(text, turn_id)
    turn_id = entry.receipts.snapshot(steer_id)["turn_id"]
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    if numeric_chat_session_id(key) is not None:
        steer_receipts.track(key, entry.receipts, steer_id)

    def _out() -> Dict[str, Any]:
        body = {"steered": True, "agent": entry.agent_id, "text": text, "steer_id": steer_id}
        if entry.receipts.delivered(steer_id):
            body["delivered"] = True
        if turn_id:
            body["turn_id"] = turn_id
        return body

    def _failed(reason: str, **extra: Any) -> Dict[str, Any]:
        # An echo that already arrived proves the agent read the text: reporting
        # failure here would make the client send it a second time.
        if entry.receipts.accepted(steer_id, False):
            return _out()
        steer_receipts.discard(steer_id)
        return {"steered": False, "reason": reason, "agent": entry.agent_id, **extra}

    try:
        fut = _invoke_send(entry.send, text, steer_id)
        ok, err = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return _failed("timeout")
    except Exception as exc:
        return _failed("error", error=str(exc))
    entry.receipts.accepted(steer_id, ok)
    if not ok:
        return _failed("rejected", error=err)
    return _out()


def _invoke_send(send: SteerSend, text: str, steer_id: str) -> "concurrent.futures.Future":
    """Call the runner's send callback, passing ``steer_id`` when it takes one."""
    try:
        params = inspect.signature(send).parameters
        takes_id = len(params) >= 2 or any(
            p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params.values()
        )
    except (TypeError, ValueError):
        takes_id = True
    return send(text, steer_id) if takes_id else send(text)


def _owner(chat_session_id: Any, token: Optional[int]) -> Optional[_Entry]:
    """The registration that owns this turn, or None for a stale observer."""
    if token is None:
        return None
    with _lock:
        entry = _active.get(_key(chat_session_id))
    return entry if entry is not None and entry.token == token else None


def note_echo(chat_session_id: Any, token: Optional[int], steer_id: Optional[str]) -> None:
    """The runner's echo matcher confirmed the agent read this exact send.

    Vendor event parsing stays in the runners: this owner is told *which*
    ``steer_id`` was echoed, so the receipt and the runner's own
    ``undelivered_steers`` can never disagree.
    """
    entry = _owner(chat_session_id, token)
    if entry is not None and steer_id:
        entry.receipts.echo(steer_id)


def note_output_start(chat_session_id: Any, token: Optional[int], item_id: Any) -> None:
    """An agent output item started (only later items can be a steer's reply)."""
    entry = _owner(chat_session_id, token)
    if entry is not None:
        entry.receipts.output_start(item_id)


def note_output_text(chat_session_id: Any, token: Optional[int], item_id: Any, text: Any) -> None:
    """A finished agent output item's text, for reply attribution."""
    entry = _owner(chat_session_id, token)
    if entry is not None:
        entry.receipts.output_text(item_id, text)
