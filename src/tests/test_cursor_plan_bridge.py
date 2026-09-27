"""Cursor CreatePlan → Cuttle chat bridge."""

from __future__ import annotations

from api.cursor_plan_bridge import (
    bridge_create_plan_into_reply,
    extract_create_plan_from_tool_call,
    format_create_plan_for_chat,
    normalize_create_plan_payload,
)


SAMPLE_PLAN = {
    "name": "Chat widgets framework",
    "overview": "Add a durable widgets strip above the composer.",
    "plan": (
        "# Chat widgets framework + Tasks\n\n"
        "## Opinion (locked defaults)\n\n"
        "Widgets are durable agent-authored state."
    ),
    "todos": [
        {"id": "backend", "content": "Add chat_widgets store + REST"},
        {"id": "frontend", "content": "Add #chatWidgetsStrip UI"},
    ],
}


def test_extract_create_plan_stream_json_shape():
    tc = {
        "createPlanToolCall": {
            "args": SAMPLE_PLAN,
        }
    }
    found = extract_create_plan_from_tool_call(tc)
    assert found is not None
    assert found["name"] == "Chat widgets framework"
    assert "durable agent-authored" in found["plan"]
    assert len(found["todos"]) == 2


def test_extract_create_plan_flat_name_shape():
    tc = {"name": "CreatePlan", "arguments": SAMPLE_PLAN}
    found = extract_create_plan_from_tool_call(tc)
    assert found is not None
    assert found["overview"].startswith("Add a durable")


def test_normalize_rejects_empty():
    assert normalize_create_plan_payload({"name": "x"}) is None


def test_format_includes_markdown_and_action_form():
    text = format_create_plan_for_chat(SAMPLE_PLAN)
    assert "## Plan: Chat widgets framework" in text
    assert "Add a durable widgets strip" in text
    assert "- [ ] Add chat_widgets store + REST" in text
    assert "<cuttle_action_form>" in text
    assert "Looks good — implement it" in text
    assert '"resume": true' in text  # must auto-send so Cuttle starts a turn
    assert '"action"' not in text  # Q&A form — no invented actions


def test_bridge_replaces_plan_created_stub():
    out = bridge_create_plan_into_reply("Plan created successfully", SAMPLE_PLAN)
    assert "## Plan: Chat widgets framework" in out
    assert "Plan created successfully" not in out
    assert "<cuttle_action_form>" in out


def test_bridge_preserves_think_block_when_replacing_stub():
    assembled = "<think>\nResearching composer UI.\n</think>\n\nPlan created successfully."
    out = bridge_create_plan_into_reply(assembled, SAMPLE_PLAN)
    assert out.startswith("<think>\nResearching composer UI.\n</think>")
    assert "## Plan: Chat widgets framework" in out.split("</think>", 1)[1]


def test_bridge_appends_when_reply_has_other_content():
    assembled = "Here is the approach at a high level."
    out = bridge_create_plan_into_reply(assembled, SAMPLE_PLAN)
    assert out.startswith("Here is the approach")
    assert "## Plan: Chat widgets framework" in out


def test_bridge_does_not_duplicate_plan_body():
    already = format_create_plan_for_chat(SAMPLE_PLAN)
    out = bridge_create_plan_into_reply(already, SAMPLE_PLAN)
    assert out.count("# Chat widgets framework + Tasks") == 1
    assert out.count("<cuttle_action_form>") == 1


def test_bridge_noop_without_plan():
    assert bridge_create_plan_into_reply("hello", None) == "hello"
