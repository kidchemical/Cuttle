"""Constants and small types for Cuttle sub-agent child chats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

COLLECT_ALL = "all"
COLLECT_FIRST = "first"
COLLECT_SERIAL = "serial"
COLLECT_MODES = (COLLECT_ALL, COLLECT_FIRST, COLLECT_SERIAL)

LIFETIME_ONE_SHOT = "one_shot"
LIFETIME_CONVERSATIONAL = "conversational"
LIFETIMES = (LIFETIME_ONE_SHOT, LIFETIME_CONVERSATIONAL)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
STATUS_CLOSED = "closed"

TERMINAL_CHILD = (STATUS_DONE, STATUS_CANCELLED, STATUS_FAILED)
TERMINAL_BATCH = (STATUS_DONE, STATUS_CANCELLED, STATUS_FAILED, STATUS_CLOSED)

MAX_CHILDREN = 8
MAX_DEPTH = 3
DEFAULT_TIMEOUT_SEC = 900.0
DEFAULT_POLL_SEC = 0.35

# A child row left non-terminal because the process running its turn died.
ORPHAN_ERROR = (
    "Sub-agent turn ended without a reply: the process running it exited "
    "(cancelled, timed out, or killed) before it could report back."
)

ORIGIN_SUBAGENT = "subagent"


def chat_handle(session_id: int) -> str:
    return f"CH-{int(session_id):06d}"


def normalize_collect(raw: Any) -> str:
    s = str(raw or COLLECT_ALL).strip().lower()
    aliases = {
        "wait_all": COLLECT_ALL,
        "all": COLLECT_ALL,
        "first": COLLECT_FIRST,
        "first_wins": COLLECT_FIRST,
        "any": COLLECT_FIRST,
        "abort_others": COLLECT_FIRST,
        "serial": COLLECT_SERIAL,
        "sequential": COLLECT_SERIAL,
        "one_by_one": COLLECT_SERIAL,
    }
    return aliases.get(s, COLLECT_ALL if s not in COLLECT_MODES else s)


def normalize_lifetime(raw: Any) -> str:
    s = str(raw or LIFETIME_ONE_SHOT).strip().lower()
    aliases = {
        "oneshot": LIFETIME_ONE_SHOT,
        "one-shot": LIFETIME_ONE_SHOT,
        "one_shot": LIFETIME_ONE_SHOT,
        "conversational": LIFETIME_CONVERSATIONAL,
        "conversation": LIFETIME_CONVERSATIONAL,
        "until_done": LIFETIME_CONVERSATIONAL,
    }
    return aliases.get(s, LIFETIME_ONE_SHOT if s not in LIFETIMES else s)


@dataclass
class ChildSpec:
    title: str
    message: str
    agent: str = "cursor"
    model: str = ""
    effort: str = ""
    route: bool = False
    profile_id: str = ""
    display_name: str = ""
    avatar: str = ""
    ephemeral_profile: bool = False
    agent_explicit: bool = False
    model_explicit: bool = False
    effort_explicit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "message": self.message,
            "agent": self.agent,
            "model": self.model,
            "effort": self.effort,
            "route": self.route,
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "avatar": self.avatar,
        }


@dataclass
class ChildRecord:
    id: str
    batch_id: str
    session_id: int
    sort_index: int = 0
    label: str = ""
    agent: str = "cursor"
    model: str = ""
    effort: str = ""
    prompt: str = ""
    status: str = STATUS_PENDING
    result: str = ""
    error: str = ""
    pid: Optional[int] = None
    owner_pid: Optional[int] = None
    query_id: str = ""
    profile_id: str = ""
    display_name: str = ""
    avatar: str = ""
    started_at: str = ""
    finished_at: str = ""
    live_status: str = ""
    live_status_at: str = ""
    live_turn_id: str = ""

    def handle(self) -> str:
        return chat_handle(self.session_id)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "session_id": self.session_id,
            "handle": self.handle(),
            "label": self.label,
            "agent": self.agent,
            "model": self.model,
            "effort": self.effort,
            "status": self.status,
            "generating": self.status in (STATUS_PENDING, STATUS_RUNNING),
            "result": self.result,
            "error": self.error,
            "query_id": self.query_id or None,
            "live_status": self.live_status if self.status == STATUS_RUNNING else "",
            "live_status_at": self.live_status_at if self.status == STATUS_RUNNING else "",
            "profile_id": self.profile_id,
            "display_name": self.display_name or self.label,
            "avatar": self.avatar,
        }


@dataclass
class BatchRecord:
    id: str
    parent_session_id: int
    user_id: int
    collect: str = COLLECT_ALL
    lifetime: str = LIFETIME_ONE_SHOT
    status: str = STATUS_RUNNING
    watch_id: str = ""
    widget_id: str = ""
    attach_message_id: Optional[int] = None
    children: List[ChildRecord] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "parent_session_id": self.parent_session_id,
            "parent_handle": chat_handle(self.parent_session_id),
            "collect": self.collect,
            "lifetime": self.lifetime,
            "status": self.status,
            "watch_id": self.watch_id or None,
            "widget_id": self.widget_id or None,
            "children": [c.public() for c in self.children],
        }
