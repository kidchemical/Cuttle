"""Supervised control-lane classification (status / cancel / followup).

Control commands must execute immediately while a worker runs — they are not
ordinary chat prompts and must not wait in the pending-prompt queue.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

_COORDINATE_RE = re.compile(r"^/coordinate(?:\s+(.*))?$", re.I | re.DOTALL)
_COORDINATOR_RE = re.compile(r"^/coordinator(?:\s+(.*))?$", re.I | re.DOTALL)

# Subcommands that never start a worker / never call a model.
_COORDINATE_CONTROL = frozenset(
    {
        "status",
        "show",
        "cancel",
        "stop",
        "abort",
        "followup",
        "follow-up",
        "follow_up",
    }
)

_COORDINATOR_CONTROL_HEADS = frozenset(
    {
        "",
        "status",
        "show",
        "mode",
        "profile",
        "worker",
        "review-loops",
        "review_loops",
        "followups",
        "reset",
    }
)


def strip_sticky_agent_prefix(message: str) -> str:
    """Drop a leading sticky agent chip if a control command follows."""
    text = (message or "").strip()
    if not text.startswith("/"):
        return text
    # /cursor /coordinate status  → /coordinate status
    m = re.match(
        r"^/(?:cursor|codex|claude|hermes|muse|deepseek|claw|opencode|antigravity)(?:\s+[^\s/]+)?\s+(/coordinate\b.*|/coordinator\b.*)$",
        text,
        re.I | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return text


def parse_coordinate_args(message: str) -> Optional[str]:
    m = _COORDINATE_RE.match(strip_sticky_agent_prefix(message))
    if not m:
        return None
    return (m.group(1) or "").strip()


def parse_coordinator_args(message: str) -> Optional[str]:
    m = _COORDINATOR_RE.match(strip_sticky_agent_prefix(message))
    if not m:
        return None
    return (m.group(1) or "").strip()


def coordinate_control_kind(args: str) -> Optional[str]:
    """Return status|cancel|followup for control lane, else None (task start)."""
    text = (args or "").strip()
    if not text:
        return "status"
    low = text.lower()
    first = low.split(None, 1)[0]
    if first in ("status", "show"):
        return "status"
    if first in ("cancel", "stop", "abort"):
        return "cancel"
    if first in ("followup", "follow-up", "follow_up"):
        return "followup"
    return None


def is_coordinate_control(args: str) -> bool:
    return coordinate_control_kind(args) is not None


def is_coordinator_control(args: Optional[str]) -> bool:
    if args is None:
        return False
    text = (args or "").strip()
    if not text:
        return True
    head = text.split(None, 1)[0].lower()
    return head in _COORDINATOR_CONTROL_HEADS


def is_supervised_control_message(message: str) -> bool:
    """True for model-free / immediate supervised control commands."""
    text = strip_sticky_agent_prefix(message)
    coord_args = parse_coordinator_args(text)
    if coord_args is not None and is_coordinator_control(coord_args):
        return True
    task_args = parse_coordinate_args(text)
    if task_args is not None and is_coordinate_control(task_args):
        return True
    return False


def classify_supervised_message(message: str) -> Tuple[str, Optional[str]]:
    """
    Returns (kind, args) where kind is:
      coordinator_control | coordinate_control | coordinate_start | none
    """
    text = strip_sticky_agent_prefix(message)
    c_args = parse_coordinator_args(text)
    if c_args is not None:
        return ("coordinator_control", c_args)
    t_args = parse_coordinate_args(text)
    if t_args is not None:
        if is_coordinate_control(t_args):
            return ("coordinate_control", t_args)
        return ("coordinate_start", t_args)
    return ("none", None)


def followup_instruction(args: str) -> str:
    text = (args or "").strip()
    parts = text.split(None, 1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def session_owns_task(session_id: Any, task: Any) -> bool:
    """Prevent cross-session control of another chat's supervised task."""
    if task is None or session_id is None:
        return False
    parent = str(getattr(task, "parent_session_id", "") or "")
    sid = str(session_id)
    if not parent or not sid:
        return False
    if parent == sid:
        return True
    # Alias db_session_N <-> N
    def _bare(s: str) -> str:
        return s[len("db_session_") :] if s.startswith("db_session_") else s

    return _bare(parent) == _bare(sid)


def control_ok(response: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "success": True,
        "response": response,
        "type": "supervised_control",
        "control_lane": True,
        "native_command": extra.pop("native_command", "/coordinate"),
    }
    body.update(extra)
    return body
