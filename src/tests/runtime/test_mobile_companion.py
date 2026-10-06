"""LAN mobile notification stream: chat_complete / interaction SSE + reply."""

from __future__ import annotations

import json

import pytest

from api import mobile_companion as mc


@pytest.fixture(autouse=True)
def _reset_mobile_state():
    with mc._device_lock:
        mc._device_queues.clear()
    with mc._interactions_lock:
        mc._interactions.clear()
    yield
    with mc._device_lock:
        mc._device_queues.clear()
    with mc._interactions_lock:
        mc._interactions.clear()


def test_visible_reply_snippet_strips_thinking():
    assert mc.visible_reply_snippet(
        "<think>\nI'll inspect the listener.\n</think>\n\nYou will see a snippet."
    ) == "You will see a snippet."
    body = mc.visible_reply_snippet(
        "<thinking>\nhmm phrases about the code\n</thinking>\n\nThe real answer is here."
    )
    assert "hmm phrases" not in body
    assert "real answer" in body
    assert mc.visible_reply_snippet("<thinking>\nstuck in a loop") == "Reply ready"


def test_chat_complete_payload_uses_visible_answer():
    payload = mc.chat_complete_payload(
        "CH-000185",
        {
            "response": "<think>\nI'll look around.\n</think>\n\nFixed the snippet.",
            "pipeline": "cursor",
        },
    )
    assert payload["session_id"] == "185"
    assert payload["response"] == "Fixed the snippet."
    assert payload["pipeline"] == "cursor"


def test_chat_complete_payload_does_not_prefer_agent_session_id():
    payload = mc.chat_complete_payload(
        "196",
        {"session_id": "opencode-prior-sid", "response": "kept", "type": "cursor"},
    )
    assert payload["session_id"] == "196"


def test_emit_chat_complete_reaches_registered_device():
    q = mc.register_device_queue("phone-1")
    mc.emit_mobile_event("chat_complete", {"session_id": "185", "response": "done"})
    ev = q.get(timeout=1)
    assert ev.type == "chat_complete"
    data = json.loads(ev.to_sse_data())
    assert data["session_id"] == "185"
    assert data["response"] == "done"


def test_interaction_roundtrip():
    iid = mc.create_interaction(question="Restart Flask?", session_id="185", timeout_s=30)
    q = mc.register_device_queue("phone-1")
    # create_interaction emits before we registered; emit again to the live queue
    mc.emit_mobile_event("interaction", {"interaction_id": iid, "question": "Restart Flask?"})
    ev = q.get(timeout=1)
    assert ev.type == "interaction"
    assert mc.submit_interaction_answer(iid, "Yes") is True
    assert mc.wait_for_interaction_answer(iid, timeout_s=1) == "Yes"


def test_mobile_events_route_registered():
    from api import web_chat_api as wca

    rules = {rule.rule for rule in wca.app.url_map.iter_rules()}
    assert "/api/mobile/events" in rules
    assert "/api/mobile/poll" in rules
    assert "/api/mobile/reply" in rules
    assert "/api/mobile/android" in rules
    assert "/api/mobile/android/app-debug.apk" in rules


def test_mobile_events_requires_device_and_token():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    missing_device = client.get("/api/mobile/events?token=dev-local-token")
    assert missing_device.status_code == 400
    unauthorized = client.get("/api/mobile/events?device_id=phone-1&token=wrong")
    assert unauthorized.status_code == 401


def test_generate_chat_stream_emits_chat_complete():
    """Harness replies (Cursor etc.) finish in _generate_chat_stream, not process_message_with_bot."""
    from api import chat_delivery
    from api import web_chat_api as wca

    sid = "mobile-stream-test"
    try:
        chat_delivery.end(sid)
    except Exception:
        pass
    q = mc.register_device_queue("phone-stream")

    def process_fn(status_queue=None):
        return {"success": True, "response": "hello from cursor", "type": "cursor"}

    chunks = list(wca._generate_chat_stream(process_fn, sid))
    assert chunks
    ev = q.get(timeout=8)
    assert ev.type == "chat_complete"
    data = json.loads(ev.to_sse_data())
    assert "hello from cursor" in data["response"]


def test_mobile_poll_returns_queued_event():
    from api import web_chat_api as wca

    token = mc.get_mobile_token()
    client = wca.app.test_client()
    empty = client.get(f"/api/mobile/poll?device_id=poll-phone&token={token}&timeout=0")
    assert empty.status_code == 200
    assert empty.get_json()["events"] == []
    mc.emit_mobile_event("chat_complete", {"response": "poll-hi"})
    filled = client.get(f"/api/mobile/poll?device_id=poll-phone&token={token}&timeout=0")
    assert filled.status_code == 200
    events = filled.get_json()["events"]
    assert events and events[0]["type"] == "chat_complete"
    assert events[0]["response"] == "poll-hi"

