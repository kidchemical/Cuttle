"""Carry a turn's reasoning in its reply as one collapsed ``<think>`` block.

Every harness adapter reports reasoning through ``TextActivityLog`` (query
events of kind ``thinking``). Models sometimes put user-facing text there
(CH-001998-18), so the kernel keeps it in the saved reply where the UI renders
it collapsed. Agent-facing transcripts strip ``<think>`` (``api.transcript_text``),
so this never reaches another model.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List

_LEAD_THINK = re.compile(r"^\s*<think>\n?([\s\S]*?)\n?</think>\s*")
_TAG = re.compile(r"</?think>")
# Router, persistence and recovery classify a reply by these leading markers;
# a reply carrying one is left exactly as the adapter wrote it.
_STATUS_PREFIXES = ("[CANCELLED]", "[FAIL]", "\u274c")


def turn_thinking(tracker: Any) -> List[str]:
    """Full top-level reasoning blocks recorded for this turn, in order."""
    data = getattr(tracker, "execution_data", None) or {}
    out: List[str] = []
    for event in data.get("events") or []:
        if (isinstance(event, dict) and event.get("kind") == "thinking"
                and event.get("agent_text") and not event.get("nested")):
            text = str(event.get("text") or "").strip()
            if text and text not in out:
                out.append(text)
    return out


def with_thinking(thoughts: Iterable[str], text: str) -> str:
    """``text`` behind one ``<think>`` block holding ``thoughts``.

    Blocks already in the reply's leading ``<think>`` are skipped; an existing leading
    ``<think>`` block (Cursor progress narration) is merged, reasoning first.
    """
    text = (text or "").strip()
    if text.startswith(_STATUS_PREFIXES):
        return text
    lead = _LEAD_THINK.match(text)
    carried = lead.group(1) if lead else ""
    new = [t.strip() for t in thoughts or [] if t and t.strip() and t.strip() not in carried]
    if not new:
        return text
    body = _TAG.sub(lambda m: m.group(0).replace("<", "&lt;"), "\n\n".join(new))
    if lead:
        body = body + "\n\n" + carried.strip()
        text = text[lead.end():]
    return f"<think>\n{body}\n</think>\n\n{text}".strip()
