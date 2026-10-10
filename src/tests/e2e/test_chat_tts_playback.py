"""Bubble speaker (TTS) playback in a real browser.

Characterizes the bubble TTS owner (`chat_tts.js`) through the production chat
page: the speaker button fetches `/api/chat/tts` once per bubble, plays it on
the single shared audio channel, toggles stop, re-plays from cache, lays out
the spoken Transcript above a collapsed "Full summary", resets on failure, and
hides when Settings turn chat TTS off. Isolated API, no Flask, model or
provider calls.
"""
import base64
import re
import json
import struct
import time
from urllib.parse import urlparse

from playwright.sync_api import expect

from .test_chat_action_card_render import CardWorld
from .test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401


def _wav(seconds, rate=8000):
    """Silent 16-bit mono WAV."""
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return header + b"data" + struct.pack("<I", len(data)) + data


AUDIO_B64 = base64.b64encode(_wav(4)).decode()
SHORT_AUDIO_B64 = base64.b64encode(_wav(0.2)).decode()


# Host realm: the chat iframe posts toasts to its shell parent.
TOAST_PROBE = """
(() => {
  if (window.top !== window) return;
  window.__toasts = [];
  window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'cuttle-toast') window.__toasts.push(e.data);
  });
})();
"""


def _tip(locator):
    """Button tooltip text (ui_boot moves `title` into `data-tooltip`)."""
    return locator.get_attribute("title") or locator.get_attribute("data-tooltip")


def _open_tts_chat(browser, static_server, tts_replies, prefs=None):
    """Chat with one assistant bubble; `tts_replies` are popped per TTS request."""
    world = CardWorld([])
    world.history.append({"id": 2, "role": "assistant",
                          "content": "First paragraph of the answer.\n\nSecond paragraph.",
                          "timestamp": time.time() * 1000})
    tts_calls = []

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/api/chat/tts":
            tts_calls.append(json.loads(route.request.post_data or "{}"))
            status, body = tts_replies.pop(0)
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
            return
        if path == "/api/settings/chat-tts" and prefs is not None:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "chat_tts": prefs}))
            return
        world.handle(route)

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.add_init_script(TOAST_PROBE)
    apply_request_guard(page.context, static_server)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", handle)
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             " src=\"/chat_page.html?chat=42\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator(".message.assistant").first.wait_for(state="visible")
    return page, frame, errors, tts_calls


def _ok(audio_b64, spoken="Short spoken summary.", summarized=True):
    return (200, {"success": True, "audio_base64": audio_b64, "content_type": "audio/wav",
                  "spoken_text": spoken, "summarized": summarized})


def test_speaker_plays_toggles_stop_and_replays_from_cache(browser, static_server):
    page, frame, errors, tts_calls = _open_tts_chat(browser, static_server, [_ok(AUDIO_B64)])
    try:
        bubble = frame.locator(".message.assistant").first
        btn = bubble.locator(".message-tts-btn")
        expect(btn).to_be_visible()
        assert _tip(btn) == "Listen (summarize + speak)"
        btn.click()
        expect(btn).to_have_class("message-tts-btn message-icon-btn is-playing")
        assert _tip(btn) == "Playing summary — click to stop"
        assert len(tts_calls) == 1
        assert "First paragraph" in tts_calls[0]["text"]
        # Transcript (spoken text) above, original body collapsed under Full summary.
        transcript = bubble.locator(".message-content > .tts-transcript-block")
        expect(transcript).to_have_attribute("open", "")
        expect(transcript.locator(".tts-transcript-body")).to_have_text("Short spoken summary.")
        full = bubble.locator(".message-content > .tts-full-summary-block")
        assert full.evaluate("el => !el.open")
        expect(full.locator(".tts-full-summary-body")).to_contain_text("Second paragraph.")
        assert bubble.evaluate("el => el.classList.contains('has-tts-transcript')")
        # Click again stops and resets the button.
        btn.click()
        expect(btn).to_have_class("message-tts-btn message-icon-btn")
        assert _tip(btn) == "Listen (summarize + speak)"
        # Replay uses the cached audio: no second request, layout not duplicated.
        btn.click()
        expect(btn).to_have_class("message-tts-btn message-icon-btn is-playing")
        assert len(tts_calls) == 1
        expect(bubble.locator(".tts-transcript-block")).to_have_count(1)
        btn.click()
        assert not errors
    finally:
        page.close()


def test_speaker_resets_after_short_clip_ends(browser, static_server):
    page, frame, errors, _calls = _open_tts_chat(
        browser, static_server, [_ok(SHORT_AUDIO_B64, summarized=False)])
    try:
        btn = frame.locator(".message.assistant .message-tts-btn").first
        btn.click()
        # Natural end returns the button to its idle state.
        expect(btn).to_have_class("message-tts-btn message-icon-btn", timeout=8000)
        assert btn.is_enabled()
        assert not errors
    finally:
        page.close()


def test_speaker_failure_resets_button_and_toasts(browser, static_server):
    page, frame, errors, _calls = _open_tts_chat(
        browser, static_server, [(500, {"success": False, "error": "TTS provider down"})])
    try:
        btn = frame.locator(".message.assistant .message-tts-btn").first
        btn.click()
        expect(btn).to_have_class("message-tts-btn message-icon-btn")
        page.wait_for_function("window.__toasts.length > 0")
        toasts = page.evaluate("window.__toasts.map(t => [t.message, t.variant])")
        assert ["TTS provider down", "error"] in toasts
        assert btn.is_enabled()
        expect(frame.locator(".tts-transcript-block")).to_have_count(0)
        assert not errors
    finally:
        page.close()


def test_chat_tts_disabled_in_settings_hides_speakers(browser, static_server):
    page, frame, errors, _calls = _open_tts_chat(
        browser, static_server, [], prefs={"enabled": False})
    try:
        expect(frame.locator("#chatMessages")).to_have_class(
            re.compile(r"\bchat-tts-disabled\b"))
        expect(frame.locator(".message.assistant .message-tts-btn")).to_be_hidden()
        assert not errors
    finally:
        page.close()
