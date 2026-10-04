"""Harness-neutral question recovery; adapters own native event recognition.

No live RPC is retained. A recovered question is delivered as a normal action
form; its answer starts the next turn through the existing session resume path.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def normalize_question_payload(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, dict):
        return None
    for key in ("input", "arguments", "args", "parameters"):
        nested = raw.get(key)
        if isinstance(nested, (dict, str)):
            found = normalize_question_payload(nested)
            if found:
                return found
    rows = raw.get("questions")
    if not isinstance(rows, list) or not rows:
        return None
    questions = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        prompt = str(row.get("prompt") or row.get("question") or row.get("title") or "").strip()
        if not prompt:
            return None
        options = []
        values = row.get("options") or []
        if not isinstance(values, list):
            return None
        for j, option in enumerate(values):
            if isinstance(option, dict):
                label = str(option.get("label") or option.get("id") or option.get("value") or "").strip()
                oid = str(option.get("id") or option.get("value") or f"opt_{j}")
            elif isinstance(option, str):
                oid = label = option.strip()
            else:
                return None
            if not label:
                return None
            # The card's Cancel button is distinct from a native answer labelled
            # "cancel". Choice ids must also be unique after normalization.
            used_ids = {o["id"] for o in options}
            if oid.lower() == "cancel" or oid in used_ids:
                oid = f"opt_{j}"
                suffix = 1
                while oid in used_ids:
                    oid = f"opt_{j}_{suffix}"
                    suffix += 1
            options.append({"id": oid, "label": label})
        questions.append({
            "id": str(row.get("id") or f"q{i + 1}"),
            "prompt": prompt,
            "options": options,
            "multiple": bool(row.get("multiple") or row.get("multiSelect")
                             or row.get("allow_multiple") or row.get("allowMultiple")),
        })
    return {"title": str(raw.get("title") or "").strip(), "questions": questions}


def question_to_form(payload: Dict[str, Any]) -> Dict[str, Any]:
    questions = payload["questions"]
    title = payload.get("title") or ""
    base = {"lock": "form", "silent": True, "resume": True}
    if len(questions) == 1 and questions[0]["options"]:
        q = questions[0]
        spec = {
            **base,
            "mode": "multi" if q["multiple"] else "choice",
            "title": title or q["prompt"],
            "options": q["options"],
        }
        if title:
            spec["description"] = q["prompt"]
        if q["multiple"]:
            spec["submitLabel"] = "Submit"
        return spec
    fields = []
    for i, q in enumerate(questions):
        field_type = "checkboxes" if q["multiple"] else "radio"
        fields.append({
            "id": f"q{i + 1}",
            "label": q["prompt"],
            "type": field_type if q["options"] else "text",
            "options": [{"value": o["id"], "label": o["label"]} for o in q["options"]],
        })
    return {
        **base,
        "mode": "form",
        "title": title or "Questions",
        "submitLabel": "Submit",
        "fields": fields,
    }


def bridge_question_into_reply(text: str, payload: Optional[Dict[str, Any]]) -> str:
    if not payload:
        return text or ""
    bridge = QuestionBridge()
    bridge.capture(payload)
    return bridge.render(text)


class QuestionBridge:
    """Turn-local recovered questions, deduplicated across start/end events."""

    def __init__(self) -> None:
        self.questions: list[dict] = []
        self.invalid = False
        self.title = ""

    def capture(self, raw: Any) -> None:
        payload = normalize_question_payload(raw)
        if payload is None:
            self.invalid = True
            return
        self.title = self.title or payload.get("title") or ""
        for q in payload["questions"]:
            if not any((q["prompt"], q["options"], q["multiple"]) ==
                       (old["prompt"], old["options"], old["multiple"]) for old in self.questions):
                self.questions.append(q)

    @property
    def pending(self) -> bool:
        return bool(self.questions or self.invalid)

    def render(self, text: str) -> str:
        text = text or ""
        if not self.pending:
            return text
        visible = text.split("</think>", 1)[-1]
        covered = set()
        from api.action_forms import is_qa_resume_spec, normalize_action_form_spec

        pattern = r"<cuttle_action_form(?:_pending\b[^>]*)?>\s*(.*?)</cuttle_action_form(?:_pending)?>"
        for match in re.finditer(pattern, visible, re.S | re.I):
            try:
                spec = normalize_action_form_spec(json.loads(match.group(1)))
            except (TypeError, ValueError):
                continue
            if not spec or not is_qa_resume_spec(spec):
                continue
            covered.update([spec.get("title"), spec.get("description")])
            covered.update(f.get("label") for f in spec.get("fields", []))
        remaining = [q for q in self.questions if q["prompt"] not in covered]
        if remaining:
            spec = question_to_form({"questions": remaining, "title": self.title})
            block = "<cuttle_action_form>\n" + json.dumps(spec, ensure_ascii=False, indent=2)
            block += "\n</cuttle_action_form>"
            text = text.rstrip() + ("\n\n" if text.strip() else "") + block
        error = ("Cuttle could not decode a native input request. No answer was supplied. "
                 "Please ask the agent to restate its question as a Cuttle form.")
        if self.invalid and error not in visible:
            text = text.rstrip() + "\n\n" + error
        return text.strip()
