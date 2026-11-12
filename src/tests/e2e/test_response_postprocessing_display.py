"""Append-mode replies fold the agent's answer into a collapsed disclosure, then the labelled processed text."""
import json

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401
from .test_chat_action_card_render import CardWorld, _open_card_chat


def test_append_collapses_original_before_labelled_summary_and_survives_reload(browser, static_server):
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
        label = bubble.locator('.response-postprocessed-label')
        expect(label).to_have_text('Added by post-processing')
        expect(bubble.get_by_text('Short processed answer', exact=True)).to_be_visible()
        assert bubble.locator('.message-content').evaluate(
            '(n) => n.querySelector("details.response-original-block").compareDocumentPosition('
            'n.querySelector(".response-postprocessed-label")) & Node.DOCUMENT_POSITION_FOLLOWING')
        disclosure.locator('summary').click()
        expect(disclosure.locator('h2')).to_have_text('Full answer')
        expect(disclosure.locator('h2')).to_be_visible()
        expect(disclosure.locator('pre code')).to_contain_text('print("hello")')
        assert bubble.locator('script').count() == 0
        copy_btn = disclosure.locator('.response-original-copy-btn')
        expect(copy_btn).to_be_visible()
        frame.locator('html').evaluate(
            '() => { window.__copied = []; navigator.clipboard.writeText = async (t) => { window.__copied.push(t); }; }')
        copy_btn.click()
        expect(copy_btn).to_have_class('response-original-copy-btn is-copied')
        assert frame.locator('html').evaluate('() => window.__copied') == [original]
        expect(disclosure).to_have_attribute('open', '')
        assert not frame.locator('html').evaluate('() => window.__originalInjected === true')
        page.reload(wait_until='domcontentloaded')
        expect(frame.locator('details.response-original-block h2').last).to_be_hidden()
        expect(frame.get_by_text('Short processed answer', exact=True)).to_be_visible()
        assert errors == []
    finally:
        page.close()


def test_thinking_renders_collapsed_before_original_response(browser, static_server):
    response = ('<think>\nThe real answer the model wrote as thinking\n</think>\n\n'
                '<cuttle_original_response>' + json.dumps('Closing line') + '</cuttle_original_response>\n\nTLDR')
    world = CardWorld([response])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator('#chatInput').fill('Go')
        frame.locator('#sendButton').click()
        bubble = frame.locator('#chatMessages .message.assistant').last
        thinking = bubble.locator('details.thinking-block')
        expect(thinking).to_have_count(1)
        expect(thinking).not_to_have_attribute('open', '')
        expect(bubble.locator('details.response-original-block')).not_to_have_attribute('open', '')
        assert bubble.locator('.message-content').evaluate(
            '(n) => n.querySelector("details.thinking-block").compareDocumentPosition('
            'n.querySelector("details.response-original-block")) & Node.DOCUMENT_POSITION_FOLLOWING')
        thinking.locator('summary').click()
        expect(thinking).to_contain_text('The real answer the model wrote as thinking')
        expect(bubble.get_by_text('TLDR', exact=True)).to_be_visible()
        assert errors == []
    finally:
        page.close()
