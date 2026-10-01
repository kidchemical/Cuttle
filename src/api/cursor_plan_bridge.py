"""Bridge Cursor ``CreatePlan`` tool calls into Cuttle chat replies.

Headless ``agent -p`` (and Cuttle's Cursor harness) only persist assistant text — so a turn that ends
on ``CreatePlan`` looks abrupt ("Plan created successfully") with no card.

This module extracts CreatePlan payloads from stream-json tool_call events and
rewrites the chat reply into markdown + an optional Q&A action form.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_PLAN_STUB_RE = re.compile(
    r"^(?:plan created successfully\.?|created the plan\.?|plan ready\.?)\s*$",
    re.IGNORECASE,
)
_CREATE_PLAN_KEY_RE = re.compile(r"^create[_]?plan(?:toolcall)?$", re.IGNORECASE)


def _as_args_dict(val: Any) -> Dict[str, Any]:
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


def normalize_create_plan_payload(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize CreatePlan tool args into ``{name, overview, plan, todos}``."""
    args = _as_args_dict(raw)
    if not args:
        return None
    # Some envelopes nest the real fields under ``input`` / ``arguments``.
    for nest_key in ("input", "arguments", "args"):
        nested = args.get(nest_key)
        if isinstance(nested, dict) and (
            nested.get("plan") or nested.get("overview") or nested.get("name")
        ):
            args = nested
            break

    plan = args.get("plan")
    overview = args.get("overview")
    name = args.get("name")
    todos = args.get("todos")

    plan_text = plan.strip() if isinstance(plan, str) else ""
    overview_text = overview.strip() if isinstance(overview, str) else ""
    name_text = name.strip() if isinstance(name, str) else ""
    if not plan_text and not overview_text:
        return None

    todo_list: List[Dict[str, Any]] = []
    if isinstance(todos, list):
        for item in todos:
            if isinstance(item, dict):
                todo_list.append(item)
            elif isinstance(item, str) and item.strip():
                todo_list.append({"content": item.strip()})

    return {
        "name": name_text or "Plan",
        "overview": overview_text,
        "plan": plan_text or overview_text,
        "todos": todo_list,
    }


def extract_create_plan_from_tool_call(tool_call: Any) -> Optional[Dict[str, Any]]:
    """Pull a CreatePlan payload from a Cursor stream-json ``tool_call`` object."""
    if not isinstance(tool_call, dict):
        return None

    # Flat / ACP-ish: {"name":"CreatePlan","arguments":{...}}
    name = tool_call.get("name") or tool_call.get("tool") or tool_call.get("toolName")
    if isinstance(name, str) and name.strip().lower() in (
        "createplan",
        "create_plan",
        "create-plan",
    ):
        for key in ("arguments", "args", "input", "parameters"):
            found = normalize_create_plan_payload(tool_call.get(key))
            if found:
                return found
        return normalize_create_plan_payload(tool_call)

    # Stream-json: {"createPlanToolCall": {"args": {...}, "result": {...}}}
    for key, val in tool_call.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            continue
        base = key[: -len("ToolCall")] if key.endswith("ToolCall") else key
        if not _CREATE_PLAN_KEY_RE.match(base) and not _CREATE_PLAN_KEY_RE.match(key):
            continue
        for nest in ("args", "arguments", "input", "parameters"):
            found = normalize_create_plan_payload(val.get(nest))
            if found:
                return found
        # Occasionally the plan lands only on the completed result envelope.
        result = val.get("result")
        if isinstance(result, dict):
            for nest in ("args", "arguments", "input", "plan", "data"):
                found = normalize_create_plan_payload(
                    result.get(nest) if nest != "plan" else result
                )
                if found:
                    return found
            found = normalize_create_plan_payload(result)
            if found:
                return found
        found = normalize_create_plan_payload(val)
        if found:
            return found
    return None


def _format_todos_md(todos: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for item in todos:
        content = (
            item.get("content")
            or item.get("text")
            or item.get("title")
            or item.get("id")
            or ""
        )
        content = str(content).strip()
        if not content:
            continue
        status = str(item.get("status") or "").strip().lower()
        checked = status in ("completed", "done", "cancelled")
        lines.append(f"- [{'x' if checked else ' '}] {content}")
    return "\n".join(lines)


def format_create_plan_for_chat(plan: Dict[str, Any]) -> str:
    """Markdown body + Q&A action form for a CreatePlan payload."""
    name = str(plan.get("name") or "Plan").strip() or "Plan"
    overview = str(plan.get("overview") or "").strip()
    body = str(plan.get("plan") or "").strip()
    todos = plan.get("todos") if isinstance(plan.get("todos"), list) else []

    parts: List[str] = [f"## Plan: {name}"]
    if overview:
        parts.append(overview)
    if body:
        # Avoid a double H1 when the plan already starts with the same title.
        parts.append(body)
    todo_md = _format_todos_md(todos)
    if todo_md:
        parts.append("### Implementation todos\n\n" + todo_md)

    parts.append(
        "Reply **go** to implement, or say what to change."
    )

    form = {
        "mode": "choice",
        "title": f"Plan: {name}",
        "lock": "form",
        "silent": True,
        # Q&A pick must resume the agent — otherwise Cuttle only toasts and
        # never starts a turn.
        "resume": True,
        "options": [
            {"id": "go", "label": "Looks good — implement it"},
            {"id": "revise", "label": "I want changes (reply with notes)"},
        ],
    }
    parts.append(
        "<cuttle_action_form>\n"
        + json.dumps(form, ensure_ascii=False, indent=2)
        + "\n</cuttle_action_form>"
    )
    return "\n\n".join(parts)


def _visible_answer(assembled: str) -> str:
    text = assembled or ""
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]
    return text.strip()


def _reply_already_has_plan(visible: str, plan: Dict[str, Any]) -> bool:
    body = str(plan.get("plan") or "").strip()
    if not body or not visible:
        return False
    # Substantial unique probe from the plan body.
    probe = re.sub(r"\s+", " ", body)[:96].strip()
    if len(probe) >= 24 and probe in re.sub(r"\s+", " ", visible):
        return True
    name = str(plan.get("name") or "").strip()
    if name and f"## Plan: {name}" in visible and body[:40] in visible:
        return True
    return False


def _is_plan_stub_reply(visible: str) -> bool:
    text = (visible or "").strip()
    if not text:
        return True
    if _PLAN_STUB_RE.match(text):
        return True
    # Short CreatePlan ack with no real plan content.
    if len(text) <= 120 and re.search(r"\bplan created\b", text, re.IGNORECASE):
        return True
    return False


def _form_block_from_formatted(formatted: str) -> str:
    idx = formatted.lower().find("<cuttle_action_form")
    if idx < 0:
        return ""
    return formatted[idx:].strip()


def bridge_create_plan_into_reply(
    assembled: str, plan: Optional[Dict[str, Any]]
) -> str:
    """Ensure CreatePlan content appears in the user-visible chat reply."""
    if not plan:
        return assembled or ""
    normalized = normalize_create_plan_payload(plan) or plan
    if not str(normalized.get("plan") or "").strip():
        return assembled or ""

    formatted = format_create_plan_for_chat(normalized)
    visible = _visible_answer(assembled)
    has_form = "<cuttle_action_form" in visible.lower()

    if _reply_already_has_plan(visible, normalized):
        if has_form:
            return assembled or formatted
        form_block = _form_block_from_formatted(formatted)
        if not form_block:
            return assembled or formatted
        preface = ""
        if "reply **go**" not in visible.lower():
            preface = "\n\nReply **go** to implement, or say what to change.\n\n"
        else:
            preface = "\n\n"
        return (assembled or "").rstrip() + preface + form_block

    if _is_plan_stub_reply(visible):
        if assembled and "</think>" in assembled:
            think = assembled.split("</think>", 1)[0] + "</think>"
            return think + "\n\n" + formatted
        return formatted

    return (assembled or "").rstrip() + "\n\n" + formatted
