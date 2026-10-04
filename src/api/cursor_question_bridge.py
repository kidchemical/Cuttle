"""Bridge Cursor ``AskQuestion`` tool calls into Cuttle action forms.

Headless ``agent -p`` has no picker UI for AskQuestion, so the tool returns "skipped" immediately and the user never sees the
question. This turns the tool args into a ``<cuttle_action_form>`` with
``resume: true`` so the pick starts the next agent turn.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Backwards-compatible entry points; conversion belongs to the shared harness owner.
from api.agent_harness.questions import (
    normalize_question_payload as normalize_ask_question_payload,
    question_to_form as ask_question_to_form,
    bridge_question_into_reply as bridge_ask_question_into_reply,
)

_ASK_KEY_RE = re.compile(r"^ask[_]?questions?$", re.IGNORECASE)


def extract_ask_question_from_tool_call(tool_call: Any) -> Optional[Dict[str, Any]]:
    """Pull an AskQuestion payload from a Cursor stream-json ``tool_call`` object."""
    if not isinstance(tool_call, dict):
        return None
    name = tool_call.get("name") or tool_call.get("tool") or tool_call.get("toolName")
    if isinstance(name, str) and _ASK_KEY_RE.match(name.strip().replace("-", "_")):
        for key in ("arguments", "args", "input", "parameters"):
            found = normalize_ask_question_payload(tool_call.get(key))
            if found:
                return found
        return normalize_ask_question_payload(tool_call)
    # Stream-json: {"askQuestionToolCall": {"args": {...}, "result": {...}}}
    for key, val in tool_call.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            continue
        base = key[: -len("ToolCall")] if key.endswith("ToolCall") else key
        if not _ASK_KEY_RE.match(base):
            continue
        for nest in ("args", "arguments", "input", "parameters"):
            found = normalize_ask_question_payload(val.get(nest))
            if found:
                return found
        found = normalize_ask_question_payload(val)
        if found:
            return found
    return None
