"""
Mobile companion support (Android phone + watch via mirrored notifications).

This module provides:
- an SSE event stream for mobile clients
- a simple interaction (question → reply) mechanism that pipeline nodes can await

Design goals:
- keep it LAN-friendly (home WiFi) and lightweight
- avoid new infrastructure (push services) for the first milestone
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

_THINK_CLOSE = re.compile(
    r"</(?:think|thinking|redacted_thinking)\s*>",
    re.IGNORECASE,
)
_THINK_BLOCK = re.compile(
    r"<(think|thinking|redacted_thinking)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_OPEN = re.compile(
    r"<(think|thinking|redacted_thinking)\b[^>]*>",
    re.IGNORECASE,
)


def normalize_mobile_session_id(session_id: Any) -> str:
    """Bare auth-db id when possible so chat_page can open the same thread."""
    if session_id is None:
        return ""
    s = str(session_id).strip()
    if not s or s.lower() == "none":
        return ""
    if s.startswith("db_session_"):
        s = s[len("db_session_") :]
    if s.upper().startswith("CH-") and s[3:].isdigit():
        return str(int(s[3:]))
    if s.isdigit():
        return str(int(s))
    return s


def visible_reply_snippet(text: Any, limit: int = 240) -> str:
    """Lock-screen body: the readable reply, not model thinking."""
    raw = text if isinstance(text, str) else ("" if text is None else str(text))
    if _THINK_CLOSE.search(raw):
        raw = _THINK_CLOSE.split(raw)[-1]
    raw = _THINK_BLOCK.sub("", raw).strip()
    if _THINK_OPEN.match(raw):
        return "Reply ready"
    raw = _THINK_OPEN.sub("", raw)
    cleaned = " ".join(raw.split()).strip()
    if not cleaned:
        return "Reply ready"
    if len(cleaned) > limit:
        return cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def chat_complete_payload(session_id: Any, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = result or {}
    # Pin to the Cuttle chat this turn ran in. Agent result dicts often carry a
    # CLI resume id in session_id (Cursor/OpenCode/native_*) — using that sent
    # the phone notification (and tap-to-open) to the wrong thread.
    sid = normalize_mobile_session_id(session_id) or normalize_mobile_session_id(
        result.get("session_id")
    )
    pipeline = result.get("pipeline") or result.get("type") or result.get("agent_id")
    return {
        "session_id": sid,
        "pipeline": pipeline,
        "query_id": result.get("query_id"),
        "report_url": result.get("report_url"),
        "response": visible_reply_snippet(result.get("response", "")),
    }


def _now() -> float:
    return time.time()


def get_mobile_token() -> str:
    """
    Shared secret token required by the phone to connect and reply.
    Set CUTTLE_MOBILE_TOKEN in environment (or <home>/.env loaded by daemon).
    """
    token = (os.environ.get("CUTTLE_MOBILE_TOKEN") or "").strip()
    return token if token else "dev-local-token"


def verify_mobile_token(token: Optional[str]) -> bool:
    if not token:
        return False
    return secrets.compare_digest(str(token), get_mobile_token())


@dataclass(frozen=True)
class MobileEvent:
    type: str
    payload: Dict[str, Any]
    ts: float

    def to_sse_data(self) -> str:
        return json.dumps({"type": self.type, "ts": self.ts, **self.payload})


# device_id -> Queue[MobileEvent]
_device_queues: Dict[str, "queue.Queue[MobileEvent]"] = {}
_device_lock = threading.Lock()


def register_device_queue(device_id: str) -> "queue.Queue[MobileEvent]":
    with _device_lock:
        q = _device_queues.get(device_id)
        if q is None:
            q = queue.Queue(maxsize=500)
            _device_queues[device_id] = q
        return q


def drain_events(device_id: str, timeout_s: float = 20.0, max_n: int = 20) -> list:
    """Wait up to timeout_s for events, then drain whatever is queued."""
    q = register_device_queue(device_id)
    out: list = []
    try:
        ev = q.get(timeout=max(0.05, float(timeout_s)))
        out.append(json.loads(ev.to_sse_data()))
    except queue.Empty:
        return out
    while len(out) < max_n:
        try:
            ev = q.get_nowait()
            out.append(json.loads(ev.to_sse_data()))
        except queue.Empty:
            break
    return out


def unregister_device_queue(device_id: str) -> None:
    with _device_lock:
        _device_queues.pop(device_id, None)


def emit_mobile_event(event_type: str, payload: Optional[Dict[str, Any]] = None, device_id: Optional[str] = None) -> None:
    """
    Emit an event to one device or broadcast to all connected devices.
    """
    ev = MobileEvent(type=event_type, payload=payload or {}, ts=_now())
    with _device_lock:
        items = list(_device_queues.items())
    if not items:
        print(f"[MOBILE] emit {event_type} — no phone connected", flush=True)
    else:
        print(f"[MOBILE] emit {event_type} → {len(items)} device(s)", flush=True)
    for did, q in items:
        if device_id and did != device_id:
            continue
        try:
            q.put_nowait(ev)
        except queue.Full:
            # Drop oldest-ish by draining a bit, then try once.
            try:
                _ = q.get_nowait()
                q.put_nowait(ev)
            except Exception:
                pass


# interaction_id -> record
_interactions: Dict[str, Dict[str, Any]] = {}
_interactions_lock = threading.Lock()
_interactions_cv = threading.Condition(_interactions_lock)


def create_interaction(
    *,
    question: str,
    choices: Optional[list[str]] = None,
    session_id: str = "",
    query_id: Optional[str] = None,
    timeout_s: int = 300,
) -> str:
    interaction_id = secrets.token_urlsafe(12)
    expires_at = _now() + max(5, int(timeout_s))
    record = {
        "interaction_id": interaction_id,
        "question": question,
        "choices": choices or ["Yes", "No"],
        "session_id": session_id,
        "query_id": query_id,
        "created_at": _now(),
        "expires_at": expires_at,
        "answered": False,
        "answer": None,
    }
    with _interactions_lock:
        _interactions[interaction_id] = record
        _interactions_cv.notify_all()

    emit_mobile_event(
        "interaction",
        {
            "interaction_id": interaction_id,
            "question": question,
            "choices": record["choices"],
            "session_id": session_id,
            "query_id": query_id,
            "expires_at": expires_at,
        },
    )
    return interaction_id


def submit_interaction_answer(interaction_id: str, answer: str) -> bool:
    with _interactions_lock:
        rec = _interactions.get(interaction_id)
        if not rec:
            return False
        rec["answered"] = True
        rec["answer"] = answer
        rec["answered_at"] = _now()
        _interactions_cv.notify_all()
        return True


def wait_for_interaction_answer(interaction_id: str, timeout_s: int = 300) -> Optional[str]:
    deadline = _now() + max(1, int(timeout_s))
    with _interactions_lock:
        while True:
            rec = _interactions.get(interaction_id)
            if not rec:
                return None
            if rec.get("answered"):
                return rec.get("answer")
            now = _now()
            if now >= deadline or now >= rec.get("expires_at", deadline):
                return None
            _interactions_cv.wait(timeout=min(1.0, max(0.05, deadline - now)))

