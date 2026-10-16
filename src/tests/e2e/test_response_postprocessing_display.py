"""Processed replies use a collapsed, safely rendered original in live/history UI."""
import json

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401
from .test_chat_action_card_render import CardWorld, _open_card_chat


def test_append_collapses_original_before_summary_and_survives_reload(browser, static_server):
    original = '## Full answer\n\n**Original explanation**\n\n```python\nprint("hello")\n```\n\n</details><script>window.__originalInjected = true</script>'
    response = '<cuttle_original_response>' + json.dumps(original) + '</cuttle_original_response>\n\n**Short processed answer**'
    world = CardWorld([response])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator('#chatInput').fill('Explain it')
        frame.locator('#sendButton').click()
        bubble = frame.locator('#chatMessages .message.assistant').last
        disclosure = bubble.locator('details.response-original-block')
        expect(disclosure).to_be_visible()
        expect(disclosure).not_to_have_attribute('open', '')
        expect(disclosure.locator('h2')).to_be_hidden()
        expect(bubble.get_by_text('Short processed answer', exact=True)).to_be_visible()
        assert bubble.locator('.message-content').evaluate('(n) => n.querySelector("details").compareDocumentPosition(n.querySelector("strong:last-child")) & Node.DOCUMENT_POSITION_FOLLOWING')
        disclosure.locator('summary').click()
        expect(disclosure.locator('h2')).to_have_text('Full answer')
        expect(disclosure.locator('h2')).to_be_visible()
        expect(disclosure.locator('pre code')).to_contain_text('print("hello")')
        assert bubble.locator('script').count() == 0
        assert not frame.locator('html').evaluate('() => window.__originalInjected === true')
        page.reload(wait_until='domcontentloaded')
        expect(frame.locator('details.response-original-block h2')).to_be_hidden()
        expect(frame.get_by_text('Short processed answer', exact=True)).to_be_visible()
        assert errors == []
    finally:
        page.close()
