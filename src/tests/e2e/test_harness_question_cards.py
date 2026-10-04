"""Recovered native questions paint in the real chat renderer, isolated APIs."""
import json

import pytest

from api.agent_harness.questions import QuestionBridge
from .test_chat_action_card_render import (
    CardWorld, _open_card_chat, _send_and_wait_card,
    browser, static_server,
)


@pytest.mark.parametrize("free_text", [False, True])
def test_recovered_native_question_is_visible(browser, static_server, free_text):
    question = {"question": "What did you observe?"} if free_text else {
        "question": "Which scope?", "options": ["Small", "Full"]}
    bridge = QuestionBridge()
    bridge.capture({"questions": [question]})
    world = CardWorld([bridge.render("Findings")])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show native question")
        assert json.loads(card.get_attribute("data-spec"))["resume"] is True
        assert card.locator(".cuttle-action-form-progress").count() == 0
        if free_text:
            field = card.locator('[data-field-id="q1"]')
            assert field.is_visible()
            field.fill("The buttons overlap")
        else:
            assert card.locator(".cuttle-action-form-title").inner_text() == "Which scope?"
            assert card.locator('[data-action-form-option="Small"]').is_visible()
        assert not errors
    finally:
        page.close()
