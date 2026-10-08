"""Voice-mode narrator: instant spoken acknowledgment + progress narration.

The narrator never does the work and never answers the request. The sticky
agent or Cuttle Router runs the turn exactly as before; this owner only turns
the user's words and the turn's live status lines into one short spoken line
(cheap completion via :mod:`api.llm_complete`, voiced with the ``chat_tts``
settings). Gated by the ``voice_narrator`` experimental flag.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

FLAG_ID = "voice_narrator"

KINDS = ("ack", "progress")
ACK_MODES = ("new", "steer", "queue")

MAX_UTTERANCE_CHARS = 1200
MAX_EVENTS = 12
MAX_EVENT_CHARS = 200
MAX_SAID = 6
MAX_LINE_CHARS = 220

_SLASH_AGENT_RE = re.compile(r"^\s*/([a-z][\w-]*)\b", re.IGNORECASE)

# The narrator speaks AS Cuttle: whichever harness runs the turn is "me" to the
# user, so prompts never name the vendor agent and forbid third person.
_VOICE = (
    "You are Cuttle, the user's hands-free assistant, speaking out loud in the "
    "first person (\"I'm on it\", \"I'm reading…\"). The work is yours: never "
    "mention an agent, tool, model, CLI or anyone else doing it, and never say "
    "\"the agent\". Plain speech only: no markdown, code, URLs or file paths."
)

_ACK_SYSTEM = (
    _VOICE + " The user just asked for something. Reply with ONE or two short, "
    "natural sentences (at most 25 words) that show you understood, say you're "
    "working on it now, and that you'll keep them posted as you go. Never "
    "answer the request, claim results or promise specifics."
)

_ACK_MODE_HINT = {
    "new": "You are starting on it now; it will take a little while.",
    "steer": "You are already in the middle of a task and are folding this into it right now.",
    "queue": "You will get to it right after the task you are finishing.",
}

_HEARTBEAT_SYSTEM = (
    _VOICE + " You are in the middle of the user's request with nothing new to "
    "report. Say ONE brief, natural line (at most 12 words) that you're still "
    "working on it. Vary the wording from earlier lines; never invent progress "
    "or results."
)

_PROGRESS_SYSTEM = (
    _VOICE + " You are working on the user's request. Given the request, your "
    "newest status lines and what you already said, reply with ONE short "
    "sentence (at most 18 words) about your newest progress. Status lines look "
    "like 'tool 3: Read chat_page.js' (an action you took), 'thinking: …' (your "
    "reasoning) or 'writing: …' (you are composing the answer). Only describe "
    "what the status lines show; never invent results or guess outcomes. Do not "
    "repeat earlier lines. Say file names, not paths. If nothing new is worth "
    "saying, reply exactly SKIP."
)


def is_enabled() -> bool:
    from api.experimental import is_enabled as flag_enabled

    return flag_enabled(FLAG_ID)


def _clip(text: Any, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def _clean_list(items: Any, limit: int, item_chars: int) -> List[str]:
    if not isinstance(items, (list, tuple)):
        return []
    out = [_clip(x, item_chars) for x in items if isinstance(x, str)]
    return [x for x in out if x][-limit:]


def strip_slash(message: str) -> str:
    return _SLASH_AGENT_RE.sub("", message or "", count=1).strip()


def _complete(user: str, system: str) -> Optional[str]:
    from api.llm_complete import complete

    return complete(user=user, system=system, max_tokens=60, temperature=0.4, timeout=8.0)


def _finish(raw: Optional[str]) -> Optional[str]:
    from api.chat_tts import clean_text_for_speech

    line = clean_text_for_speech(raw or "").strip().strip('"').strip()
    if not line or line.upper().rstrip(".") == "SKIP":
        return None
    return _clip(line, MAX_LINE_CHARS)


def ack_line(message: str, *, mode: str = "new") -> Optional[str]:
    """One spoken acknowledgment for a just-sent voice turn, or None."""
    request = _clip(strip_slash(message), MAX_UTTERANCE_CHARS)
    if not request:
        return None
    hint = _ACK_MODE_HINT.get(mode, _ACK_MODE_HINT["new"])
    user = f"User said: {request}\nContext: {hint}"
    return _finish(_complete(user, _ACK_SYSTEM))


def heartbeat_line(message: str, said: Iterable[str] = (), elapsed_sec: Any = None) -> Optional[str]:
    """One "still working" line when the agent has been quiet for a while."""
    earlier = _clean_list(list(said), MAX_SAID, MAX_LINE_CHARS)
    try:
        elapsed = max(0, int(elapsed_sec))
    except (TypeError, ValueError):
        elapsed = 0
    user = "\n".join([
        f"User asked: {_clip(strip_slash(message), MAX_UTTERANCE_CHARS)}",
        f"You have been at it for about {elapsed} seconds." if elapsed else "Still working.",
        *(["You already said:"] + [f"- {s}" for s in earlier] if earlier else []),
    ])
    return _finish(_complete(user, _HEARTBEAT_SYSTEM))


def progress_line(
    message: str, events: Iterable[str], said: Iterable[str] = (), elapsed_sec: Any = None
) -> Optional[str]:
    """One spoken progress update from live status lines; a heartbeat when there are none."""
    status = _clean_list(list(events), MAX_EVENTS, MAX_EVENT_CHARS)
    if not status:
        return heartbeat_line(message, said, elapsed_sec)
    earlier = _clean_list(list(said), MAX_SAID, MAX_LINE_CHARS)
    user = "\n".join([
        f"User asked: {_clip(strip_slash(message), MAX_UTTERANCE_CHARS)}",
        "Your newest status lines:",
        *[f"- {s}" for s in status],
        "You already said:" if earlier else "You already said: (nothing yet)",
        *[f"- {s}" for s in earlier],
    ])
    return _finish(_complete(user, _PROGRESS_SYSTEM))


def narrate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a request, write the line and voice it.

    Returns ``{"text": str|None, "audio": bytes|None}``; raises ``ValueError``
    for a malformed request and ``RuntimeError`` when speech is unavailable.
    """
    kind = str(payload.get("kind") or "")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required")
    if kind == "ack":
        mode = str(payload.get("mode") or "new")
        if mode not in ACK_MODES:
            raise ValueError(f"mode must be one of {', '.join(ACK_MODES)}")
        text = ack_line(message, mode=mode)
    else:
        text = progress_line(
            message,
            payload.get("events") or [],
            payload.get("said") or [],
            payload.get("elapsed_sec"),
        )
    if not text:
        return {"text": None, "audio": None}

    from api.chat_tts import load_chat_tts_settings, synthesize_speech

    settings = load_chat_tts_settings()
    if not settings.get("enabled", True):
        raise RuntimeError("Chat TTS is disabled in Settings")
    # Provider, voice, model, and tunables resolve from Settings.
    audio = synthesize_speech(text)
    return {"text": text, "audio": audio}
