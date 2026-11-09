"""Ordered-list numbers stay inside the clipped message box.

Outside ``::marker`` boxes sit in the list's left padding and ``.message-content``
clips overflow; with bubbles off there is no side padding, so a fixed 24px cut the leading digit of "1." in monospace themes
and of "10." in every font."""
import pytest

from .test_shared_diff_modal import browser, static_server  # noqa: F401
from .test_chat_action_card_render import CardWorld, _open_card_chat

_MARKER_FITS = """(n, font) => {
  const content = n.querySelector('.message-content');
  if (font) content.style.fontFamily = font;
  const ol = content.querySelector('ol');
  const li = ol.querySelector('li');
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;white-space:pre';
  probe.style.font = getComputedStyle(li).font;
  probe.textContent = '10. ';
  li.appendChild(probe);
  const marker = probe.getBoundingClientRect().width;
  probe.remove();
  const room = li.getBoundingClientRect().left - content.getBoundingClientRect().left;
  return {marker, room};
}"""


@pytest.mark.parametrize("bubbles", [True, False], ids=["bubbles", "flat"])
@pytest.mark.parametrize("font", ["", "monospace", "'Courier New', monospace"])
def test_two_digit_markers_fit_inside_the_message(browser, static_server, font, bubbles):
    world = CardWorld(["ok"])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        if not bubbles:  # Settings → Appearance → Message bubbles off: no side padding
            frame.locator("html").evaluate("() => localStorage.setItem('cuttleChatBubbles', '0')")
            page.reload(wait_until="domcontentloaded")
            frame.locator("#chatInput").wait_for()
            assert "cuttle-flat-messages" in frame.locator("html").get_attribute("class")
        frame.locator("#chatInput").fill("1. fix the cuttle issue\n2. kill the opencode process")
        frame.locator("#sendButton").click()
        frame.locator("#chatMessages .message.assistant").last.wait_for()
        got = frame.locator("#chatMessages .message.user").last.evaluate(_MARKER_FITS, font)
        assert got["marker"] <= got["room"], got
        assert errors == []
    finally:
        page.close()
