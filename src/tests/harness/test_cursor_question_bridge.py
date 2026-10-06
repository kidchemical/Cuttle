"""Cursor AskQuestion → Cuttle action form bridge."""

from __future__ import annotations

import json

from api.cursor_question_bridge import (
    ask_question_to_form,
    bridge_ask_question_into_reply,
    extract_ask_question_from_tool_call,
)

ONE_Q = {
    "questions": [
        {
            "id": "fix",
            "prompt": "How should we fix it?",
            "options": [{"id": "a", "label": "Option A"}, {"id": "b", "label": "Option B"}],
        }
    ]
}


def _form_json(text: str) -> dict:
    body = text.split("<cuttle_action_form>", 1)[1].split("</cuttle_action_form>", 1)[0]
    return json.loads(body)


def test_extract_stream_json_shape():
    found = extract_ask_question_from_tool_call({"askQuestionToolCall": {"args": ONE_Q}})
    assert found and found["questions"][0]["id"] == "fix"


def test_extract_flat_name_shape():
    found = extract_ask_question_from_tool_call({"name": "AskQuestion", "input": ONE_Q})
    assert found and len(found["questions"][0]["options"]) == 2


def test_unrelated_tool_ignored():
    assert extract_ask_question_from_tool_call({"readToolCall": {"args": {"path": "x"}}}) is None


def test_single_select_becomes_resuming_choice():
    spec = ask_question_to_form(extract_ask_question_from_tool_call({"name": "AskQuestion", "input": ONE_Q}))
    assert spec["mode"] == "choice"
    assert spec["resume"] is True
    assert "action" not in spec["options"][0]


def test_allow_multiple_becomes_multi_with_submit():
    payload = json.loads(json.dumps(ONE_Q))
    payload["questions"][0]["allow_multiple"] = True
    spec = ask_question_to_form(extract_ask_question_from_tool_call({"name": "AskQuestion", "input": payload}))
    assert spec["mode"] == "multi"
    assert spec["submitLabel"] == "Submit"


def test_several_questions_become_form_fields():
    payload = {
        "title": "Setup",
        "questions": [
            ONE_Q["questions"][0],
            {
                "id": "areas",
                "prompt": "Which areas?",
                "allowMultiple": True,
                "options": [{"id": "ui", "label": "UI"}, {"id": "api", "label": "API"}],
            },
        ],
    }
    spec = ask_question_to_form(extract_ask_question_from_tool_call({"name": "AskQuestion", "input": payload}))
    assert spec["mode"] == "form"
    assert [f["type"] for f in spec["fields"]] == ["radio", "checkboxes"]


def test_bridge_appends_form_once():
    payload = extract_ask_question_from_tool_call({"name": "AskQuestion", "input": ONE_Q})
    out = bridge_ask_question_into_reply("Here is the finding.", payload)
    assert out.startswith("Here is the finding.")
    assert _form_json(out)["options"][1]["label"] == "Option B"
    assert bridge_ask_question_into_reply(out, payload) == out
