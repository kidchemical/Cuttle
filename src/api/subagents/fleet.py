"""Fleet-card presentation for sub-agent children (experimental).

Pure: maps a :class:`ChildRecord` to the card payload the parent bubble
renders. The child row in ``subagent_children`` is the only source of truth;
cards carried in message metadata are refreshed from it on history load.
Gate: ``subagent_fleet_cards``. Teardown: delete this module, its calls in
``service`` / ``auth_api`` history hydration, and ``chat_subagent_fleet.js``.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from api.subagents.spec import public_launcher
from api.subagents.types import (
    ORPHAN_ERROR,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    ChildRecord,
)

SUMMARY_CHARS = 140
DETAIL_CHARS = 600

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_MARKUP = re.compile(r"<cuttle_[a-z_]+[^>]*>.*?</cuttle_[a-z_]+>", re.S)
_LEAD = re.compile(r"^\s*(?:#+|[-*+>]|\d+[.)])\s*")
_EMPH = re.compile(r"[*_`]+")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def fleet_enabled() -> bool:
    from api.experimental import is_enabled
    return is_enabled("subagent_fleet_cards")


def outcome(child: ChildRecord) -> str:
    """queued / running / done / failed / cancelled / lost (host process died)."""
    status = child.status
    if status == STATUS_PENDING:
        return "queued"
    if status == STATUS_RUNNING:
        return "running"
    if status == STATUS_DONE:
        return "done"
    if status == STATUS_CANCELLED:
        return "cancelled"
    if status == STATUS_FAILED:
        return "lost" if child.error == ORPHAN_ERROR else "failed"
    return "unknown"


def plain_text(text: str) -> str:
    """Strip code fences, Cuttle tags and inline markdown to readable prose."""
    text = _MARKUP.sub(" ", _FENCE.sub(" ", str(text or "")))
    lines = []
    for raw in text.splitlines():
        line = _EMPH.sub("", _LINK.sub(r"\1", _LEAD.sub("", raw))).strip()
        if line and not set(line) <= set("-=|:"):
            lines.append(line)
    return " ".join(lines)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fleet_entry(child: ChildRecord) -> Dict[str, Any]:
    """Launcher payload plus outcome, one-line summary and full tooltip text."""
    state = outcome(child)
    if state == "done":
        body = plain_text(child.result) or "Finished with an empty reply"
    elif state in ("failed", "lost"):
        body = plain_text(child.error) or "Failed without an error message"
    elif state == "cancelled":
        body = plain_text(child.error) or "Cancelled before replying"
    elif state == "queued":
        body = "Waiting to start"
    else:
        body = "Working…"
    return {
        **public_launcher(child.public()),
        "fleet": True,
        "outcome": state,
        "summary": _clip(body, SUMMARY_CHARS),
        "detail": _clip(body, DETAIL_CHARS),
        "started_at": child.started_at,
        "finished_at": child.finished_at,
    }
