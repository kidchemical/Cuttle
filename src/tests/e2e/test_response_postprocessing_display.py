"""Append-mode replies show the agent's full answer first, then the labelled processed text."""
import json

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401
from .test_chat_action_card_render import CardWorld, _open_card_chat


def test_append_shows_original_then_labelled_summary_and_survives_reload(browser, static_server):
    original = '## Full answer\n\n**Original explanation**\n\n```python\nprint("hello")\n```\n\n</details><script>window.__originalInjected = true</script>'
    response = '<cuttle_original_response>' + json.dumps(original) + '</cuttle_original_response>\n\n**Short processed answer**'
    world = CardWorld([response])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator('#chatInput').fill('Explain it')
        frame.locator('#sendButton').click()
        bubble = frame.locator('#chatMessages .message.assistant').last
        body = bubble.locator('.response-original-content')
        expect(body.locator('h2')).to_have_text('Full answer')
        expect(body.locator('h2')).to_be_visible()
        expect(body.locator('pre code')).to_contain_text('print("hello")')
        assert bubble.locator('details.response-original-block').count() == 0
        label = bubble.locator('.response-postprocessed-label')
        expect(label).to_have_text('Added by post-processing')
        expect(bubble.get_by_text('Short processed answer', exact=True)).to_be_visible()
        assert bubble.locator('.message-content').evaluate(
            '(n) => n.querySelector(".response-original-content h2").compareDocumentPosition('
            'n.querySelector(".response-postprocessed-label")) & Node.DOCUMENT_POSITION_FOLLOWING')
        assert bubble.locator('script').count() == 0
        assert not frame.locator('html').evaluate('() => window.__originalInjected === true')
        page.reload(wait_until='domcontentloaded')
        expect(frame.locator('.response-original-content h2').last).to_be_visible()
        expect(frame.get_by_text('Short processed answer', exact=True)).to_be_visible()
        assert errors == []
    finally:
        page.close()
