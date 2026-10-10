"""Chat media thumbs, lightbox and image context menu in a real browser.

Characterizes the media owner (`chat_media.js`) through the production chat
page inside a host frame, the same way the app shell embeds it: thumbs render
in the chat iframe, the lightbox mounts on the host document so it covers
every pane. Isolated API, no Flask or agent calls.
"""
import base64
import json
import re
import struct
import time
import zlib
from urllib.parse import urlparse

from playwright.sync_api import expect

from .test_chat_action_card_render import CardWorld
from .test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401


def _png(width, height):
    """Solid-colour RGB PNG of the given size (no image libraries needed)."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    row = b"\x00" + b"\x40\x80\xc0" * width
    raw = zlib.compress(row * height)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


BIG = _png(2400, 1600)
SMALL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=")

MEDIA_MESSAGE = (
    "Renders ready.\n\n"
    "![a render of the hero frame with lighting pass](/output/shared/hero-0123456789ab.png)\n\n"
    "![second.png](/output/shared/second.png \"Second pass, denoised\")\n\n"
    "![clip.mp4](/output/shared/clip-abcdef012345.mp4)"
)


def _open_media_chat(browser, static_server, meta_calls, init_script=None):
    """Chat page in a host frame with the media fixture message and routes."""
    world = CardWorld([])
    world.history.append({"id": 2, "role": "assistant", "content": MEDIA_MESSAGE,
                          "timestamp": time.time() * 1000})

    def handle(route):
        if urlparse(route.request.url).path == "/api/shared-media/meta":
            meta_calls.append(route.request.url)
            # Only the hero render has a staging sidecar.
            if "hero-0123456789ab.png" in route.request.url:
                body = {"success": True, "meta": {
                    "original_name": "hero.png", "original_path": "/home/u/renders/hero.png"}}
            else:
                body = {"success": False}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            return
        world.handle(route)

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    if init_script:
        page.add_init_script(init_script)
    apply_request_guard(page.context, static_server)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", handle)
    page.route(f"{static_server}/output/shared/hero-0123456789ab.png*",
               lambda r: r.fulfill(content_type="image/png", body=BIG))
    page.route(f"{static_server}/output/shared/second.png*",
               lambda r: r.fulfill(content_type="image/png", body=SMALL))
    # Missing poster/video → the thumb falls back to a <video preload=metadata> still.
    page.route(f"{static_server}/output/shared/clip-abcdef012345.*",
               lambda r: r.fulfill(status=404, body=""))
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             " src=\"/chat_page.html?chat=42\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator(".message.assistant .cuttle-media-thumb").first.wait_for(state="visible")
    return page, frame, errors


def test_media_thumbs_render_with_download_caption_and_poster_fallback(browser, static_server):
    meta_calls = []
    page, frame, errors = _open_media_chat(browser, static_server, meta_calls)
    try:
        thumbs = frame.locator(".message.assistant .cuttle-media-thumb")
        expect(thumbs).to_have_count(3)
        hero, second, clip = thumbs.nth(0), thumbs.nth(1), thumbs.nth(2)
        assert hero.get_attribute("data-kind") == "image"
        assert hero.get_attribute("data-download") == "/output/shared/hero-0123456789ab.png?download=1"
        # Markdown title becomes a figure caption; prose alt does not.
        caption = frame.locator(".message.assistant .cuttle-media-caption")
        expect(caption).to_have_count(1)
        expect(caption).to_have_text("Second pass, denoised")
        assert second.get_attribute("data-description") == "Second pass, denoised"
        # Video thumb: poster first, then the fallback swaps in a muted <video>.
        assert clip.get_attribute("data-kind") == "video"
        assert "cuttle-media-thumb--video" in (clip.get_attribute("class") or "")
        assert clip.get_attribute("data-poster") == "/output/shared/clip-abcdef012345.poster.jpg"
        expect(clip.locator("video")).to_have_count(1)
        expect(clip.locator("img.cuttle-media-poster")).to_have_count(0)
        assert clip.locator("video").evaluate("v => v.muted && v.preload === 'metadata'")
        assert not errors
    finally:
        page.close()


def test_lightbox_mounts_on_host_and_navigates_zooms_and_closes(browser, static_server):
    meta_calls = []
    page, frame, errors = _open_media_chat(browser, static_server, meta_calls)
    try:
        frame.locator(".message.assistant .cuttle-media-thumb").first.click()
        box = page.locator("#cuttle-shell-lightbox")
        expect(box).to_be_visible()
        # Lightbox lives in the host document, not inside the chat iframe.
        expect(frame.locator("#cuttle-shell-lightbox")).to_have_count(0)
        assert page.evaluate("document.documentElement.classList.contains('cuttle-lightbox-open')")
        assert frame.locator("html").evaluate("el => el.classList.contains('cuttle-lightbox-open')")
        expect(box.locator(".cuttle-lightbox-counter")).to_have_text("1 / 3")
        # Staging sidecar fills the original name and disk path.
        expect(box.locator(".cuttle-lightbox-title")).to_have_text("hero.png")
        expect(box.locator(".cuttle-lightbox-path")).to_have_text("/home/u/renders/hero.png")
        expect(box.locator(".cuttle-lightbox-desc")).to_have_text(
            "a render of the hero frame with lighting pass")
        assert any("hero-0123456789ab.png" in u for u in meta_calls)
        expect(box.locator(".cuttle-lightbox-download")).to_have_attribute("download", "hero.png")

        # Fit is the default for a large image; keyboard zoom and modes.
        toggle = box.locator(".cuttle-lightbox-zoom-toggle")
        expect(toggle).to_have_text("Fit")
        page.keyboard.press("1")
        expect(toggle).to_have_text("Actual")
        expect(box.locator(".cuttle-lightbox-stage")).to_have_class(
            "cuttle-lightbox-stage is-pannable")
        page.keyboard.press("0")
        expect(toggle).to_have_text("Fit")
        page.keyboard.press("+")
        expect(toggle).to_have_text(re.compile(r"^\d+%$"))
        box.locator('[data-lb-action="zoom-toggle"]').click()
        expect(toggle).to_have_text("Fit")

        # Arrow navigation wraps both ways; the gallery is per bubble.
        page.keyboard.press("ArrowRight")
        expect(box.locator(".cuttle-lightbox-counter")).to_have_text("2 / 3")
        expect(box.locator(".cuttle-lightbox-title")).to_have_text("second.png")
        expect(box.locator(".cuttle-lightbox-desc")).to_have_text("Second pass, denoised")
        expect(box.locator(".cuttle-lightbox-path")).to_be_hidden()
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowLeft")
        expect(box.locator(".cuttle-lightbox-counter")).to_have_text("3 / 3")
        expect(box.locator(".cuttle-lightbox-stage video")).to_have_count(1)

        page.keyboard.press("Escape")
        expect(box).to_be_hidden()
        expect(box.locator(".cuttle-lightbox-stage")).to_be_empty()
        assert not page.evaluate("document.documentElement.classList.contains('cuttle-lightbox-open')")
        assert not frame.locator("html").evaluate(
            "el => el.classList.contains('cuttle-lightbox-open')")

        # Reopen via the close button path.
        frame.locator(".message.assistant .cuttle-media-thumb").nth(1).click()
        expect(box.locator(".cuttle-lightbox-counter")).to_have_text("2 / 3")
        box.locator(".cuttle-lightbox-close").click()
        expect(box).to_be_hidden()
        assert not errors
    finally:
        page.close()


def test_image_context_menu_offers_copy_and_save_and_dismisses(browser, static_server):
    meta_calls = []
    page, frame, errors = _open_media_chat(browser, static_server, meta_calls)
    try:
        thumb = frame.locator(".message.assistant .cuttle-media-thumb").first
        thumb.click(button="right")
        menu = frame.locator(".cuttle-media-ctx-menu")
        expect(menu).to_be_visible()
        expect(menu.locator("[data-media-ctx]")).to_have_text(["Copy", "Save"])
        frame.locator("#chatMessages").press("Escape")
        expect(menu).to_have_count(0)
        # Video thumbs have no image menu.
        frame.locator(".message.assistant .cuttle-media-thumb").nth(2).click(button="right")
        expect(frame.locator(".cuttle-media-ctx-menu")).to_have_count(0)
        # Lightbox image gets the menu on the host document.
        thumb.click()
        page.locator("#cuttle-shell-lightbox .cuttle-lightbox-stage img").click(button="right")
        host_menu = page.locator(".cuttle-media-ctx-menu")
        expect(host_menu).to_be_visible()
        page.keyboard.press("Escape")
        expect(host_menu).to_have_count(0)
        # Pinned current behavior: the capture-phase dismiss clears the menu
        # before the lightbox keydown runs, so one Escape closes both.
        expect(page.locator("#cuttle-shell-lightbox")).to_be_hidden()
        assert not errors
    finally:
        page.close()


# Host realm only: record every listener the chat frame adds to the host
# window/document. Frame callbacks are cross-realm functions (not instances of
# the host's Function), which separates them from Playwright's own listeners.
HOST_LISTENER_PROBE = """
(() => {
  if (window.top !== window) return;
  const seen = [];
  window.__hostListeners = seen;
  const orig = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, fn, opts) {
    const fromFrame = typeof fn === 'function' && !(fn instanceof Function);
    if (fromFrame && (this === window || this === document || this === window.visualViewport)) {
      const signal = opts && typeof opts === 'object' ? opts.signal : undefined;
      seen.push({ type, signal });
    }
    return orig.call(this, type, fn, opts);
  };
})();
"""


def test_lightbox_host_listeners_end_with_the_chat_frame(browser, static_server):
    """Every host listener the chat frame adds is aborted when the frame unloads.

    A listener left on the shell keeps the whole unloaded chat document alive
    (renderer OOM after many chat switches), so each must ride the frame's
    teardown signal.
    """
    meta_calls = []
    page, frame, errors = _open_media_chat(
        browser, static_server, meta_calls, init_script=HOST_LISTENER_PROBE)
    try:
        frame.locator(".message.assistant .cuttle-media-thumb").first.click()
        box = page.locator("#cuttle-shell-lightbox")
        expect(box).to_be_visible()
        # Image menu on the host binds its dismiss listeners too.
        page.locator("#cuttle-shell-lightbox .cuttle-lightbox-stage img").click(button="right")
        expect(page.locator(".cuttle-media-ctx-menu")).to_be_visible()
        page.keyboard.press("Escape")
        types = page.evaluate("window.__hostListeners.map(e => e.type)")
        assert {"keydown", "resize", "pointerdown"} <= set(types), types
        page.evaluate("document.querySelector('iframe').src = 'about:blank'")
        page.wait_for_function(
            "document.querySelector('iframe').contentWindow.location.href === 'about:blank'")
        leaked = page.evaluate("""() => window.__hostListeners
            .filter(e => !(e.signal && e.signal.aborted))
            .map(e => e.type)""")
        assert leaked == [], f"host listeners outliving the chat frame: {leaked}"
        assert not errors
    finally:
        page.close()
