"""Bridge Cursor ``AskQuestion`` tool calls into Cuttle action forms.

Headless ``agent -p`` has no picker UI for AskQuestion, so the tool returns "skipped" immediately and the user never sees the
question. This turns the tool args into a ``<cuttle_action_form>`` with
``resume: true`` so the pick starts the next agent turn.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_ASK_KEY_RE = re.compile(r"^ask[_]?questions?$", re.IGNORECASE)


def _as_dict(val: Any) -> Dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip():
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def normalize_ask_question_payload(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize AskQuestion args into ``{title, questions: [...]}``."""
    args = _as_dict(raw)
    for nest_key in ("input", "arguments", "args"):
        nested = args.get(nest_key)
        if isinstance(nested, dict) and isinstance(nested.get("questions"), list):
            args = nested
            break
    questions_in = args.get("questions")
    if not isinstance(questions_in, list):
        return None
    questions: List[Dict[str, Any]] = []
    for i, q in enumerate(questions_in):
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or q.get("question") or "").strip()
        options: List[Dict[str, str]] = []
        for j, o in enumerate(q.get("options") or []):
            if isinstance(o, dict):
                oid = str(o.get("id") or o.get("value") or f"opt_{j}").strip()
                label = str(o.get("label") or oid).strip()
            else:
                oid = label = str(o).strip()
            if oid:
                options.append({"id": oid, "label": label or oid})
        if not prompt or len(options) < 2:
            continue
        questions.append(
            {
                "id": str(q.get("id") or f"q{i + 1}").strip() or f"q{i + 1}",
                "prompt": prompt,
                "options": options,
                "multiple": bool(q.get("allow_multiple") or q.get("allowMultiple")),
            }
        )
    if not questions:
        return None
    return {"title": str(args.get("title") or "").strip(), "questions": questions}


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


def ask_question_to_form(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Action-form spec: choice / multi for one question, form for several."""
    questions = payload.get("questions") or []
    title = str(payload.get("title") or "").strip()
    base: Dict[str, Any] = {"lock": "form", "silent": True, "resume": True}
    if len(questions) == 1:
        q = questions[0]
        spec = {
            **base,
            "mode": "multi" if q["multiple"] else "choice",
            "title": title or q["prompt"],
            "options": list(q["options"]),
        }
        if title:
            spec["description"] = q["prompt"]
        if q["multiple"]:
            spec["submitLabel"] = "Submit"
        return spec
    return {
        **base,
        "mode": "form",
        "title": title or "Questions",
        "submitLabel": "Submit",
        "fields": [
            {
                "id": q["id"],
                "label": q["prompt"],
                "type": "checkboxes" if q["multiple"] else "radio",
                "options": [{"value": o["id"], "label": o["label"]} for o in q["options"]],
            }
            for q in questions
        ],
    }


def bridge_ask_question_into_reply(
    assembled: str, payload: Optional[Dict[str, Any]]
) -> str:
    """Append the question card unless the reply already carries a form."""
    if not payload:
        return assembled or ""
    text = assembled or ""
    visible = text.split("</think>", 1)[-1] if "</think>" in text else text
    if "<cuttle_action_form" in visible.lower():
        return text
    block = (
        "<cuttle_action_form>\n"
        + json.dumps(ask_question_to_form(payload), ensure_ascii=False, indent=2)
        + "\n</cuttle_action_form>"
    )
    return (text.rstrip() + "\n\n" + block) if text.strip() else block
