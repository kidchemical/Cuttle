"""UI review fences using production assets and an isolated, fake API."""
import pytest

from .test_shared_diff_modal import (
    IsolatedAPI, apply_request_guard, browser, static_server,
)


@pytest.fixture
def welcome(browser, static_server):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(f"{static_server}/api/**", IsolatedAPI().handle)
    page.goto(f"{static_server}/chat_page.html", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.autoResizeWelcomeTextarea === 'function'")
    yield page
    context.close()


@pytest.mark.parametrize("width,height,keyboard,mobile", [
    (390, 420, True, True), (390, 650, True, True),
    (390, 420, False, True), (900, 440, False, False),
    (390, 440, False, False), (390, 844, False, True),
])
def test_welcome_centers_composer_and_clears_insets(welcome, width, height, keyboard, mobile):
    page = welcome
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("""([keyboard, mobile]) => {
        document.documentElement.classList.toggle('is-cuttle-mobile', mobile);
        document.documentElement.classList.toggle('keyboard-open', keyboard);
        document.documentElement.style.setProperty('--safe-area-inset-top', '24px');
        document.documentElement.style.setProperty('--safe-area-inset-bottom', '48px');
    }""", [keyboard, mobile])
    content = page.locator('.welcome-content').bounding_box()
    screen = page.locator('#welcomeScreen').bounding_box()
    assert content['y'] > 50
    assert content['y'] + content['height'] <= screen['y'] + screen['height']
    geometry = page.locator('#welcomeScreen').evaluate("""el => {
        const cs = getComputedStyle(el);
        return {top: parseFloat(cs.paddingTop), bottom: parseFloat(cs.paddingBottom)};
    }""")
    usable_center = screen['y'] + geometry['top'] + (
        screen['height'] - geometry['top'] - geometry['bottom']) / 2
    assert abs(content['y'] + content['height'] / 2 - usable_center) < 2
    assert page.locator('.welcome-logo').is_visible() == (mobile or height > 560)
    assert page.locator('.welcome-subtitle').is_visible() == (mobile or height > 560)
    if keyboard:
        assert geometry['bottom'] == 8


def test_slash_palette_click_off_and_selection(welcome):
    page = welcome
    input = page.locator('#welcomeChatInput')
    input.click()
    input.fill('/')
    menu = page.locator('#welcomeSlashCommandMenu')
    menu.wait_for(state='visible')
    input.click()
    assert menu.is_visible()
    page.mouse.click(380, 800)
    assert menu.is_hidden()
    assert input.input_value() == '/'
    input.fill('')
    input.fill('/')
    menu.wait_for(state='visible')
    menu.locator('[role="option"]').first.click()
    assert menu.is_hidden()
    assert input.input_value() != '/'


def test_history_panel_never_slides_except_on_toggle(welcome):
    """Closed history panel stays parked across the 768px sheet breakpoint.

    Regression: the hidden panel docks to opposite edges above/below
    768px, and an armed slide transition replayed that flip as a visible
    horizontal sweep on every chat-width change (pane drag, + button).
    """
    page = welcome
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    panel = page.locator("#chatHistoryPanel")

    def panel_state():
        return panel.evaluate("""el => {
            const cs = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return {
                open: el.classList.contains('open'),
                sliding: el.classList.contains('hist-slide'),
                duration: cs.transitionDuration,
                x: rect.x,
                width: rect.width,
                viewport: window.innerWidth,
            };
        }""")

    # Wide pane: closed panel carries no transition and sits fully off-edge.
    page.set_viewport_size({"width": 900, "height": 844})
    rest = panel_state()
    assert rest["open"] is False
    assert rest["duration"] in ("0s", ""), rest
    assert rest["x"] + rest["width"] <= 0, rest
    # Crossing the breakpoint must jump, never sweep: read back immediately
    # (inside the old 0.28s window) and require the docked rest pose.
    page.set_viewport_size({"width": 700, "height": 844})
    crossed = panel_state()
    assert crossed["open"] is False, crossed
    assert crossed["x"] >= crossed["viewport"], crossed
    page.set_viewport_size({"width": 900, "height": 844})
    back = panel_state()
    assert back["x"] + back["width"] <= 0, back
    # Intentional open still slides, then settles open without the class.
    page.locator("#chatHistoryPanelButton").click()
    page.wait_for_function(
        "document.getElementById('chatHistoryPanel')"
        ".classList.contains('hist-slide')",
        timeout=2000)
    page.wait_for_function(
        "document.getElementById('chatHistoryPanel')"
        ".classList.contains('open')",
        timeout=2000)
    page.wait_for_function(
        "!document.getElementById('chatHistoryPanel')"
        ".classList.contains('hist-slide')",
        timeout=5000)
    opened = panel_state()
    assert opened["open"] is True, opened
    assert opened["duration"] in ("0s", ""), opened
    # Intentional close slides shut and ends fully parked (via the
    # panel's own close button — the open sheet covers the corner one).
    page.locator("#chatHistoryPanelClose").click()
    page.wait_for_function(
        "!document.getElementById('chatHistoryPanel')"
        ".classList.contains('open')",
        timeout=5000)
    page.wait_for_function(
        "!document.getElementById('chatHistoryPanel')"
        ".classList.contains('hist-slide')",
        timeout=5000)
    shut = panel_state()
    assert shut["x"] + shut["width"] <= 0, shut
    assert errors == [], errors


def test_narrow_welcome_splash_never_overflows_pane(welcome):
    """The splash fills (up to its cap) at any pane width, never spilling.

    Regression: above 768px the splash had no width, so the flex item
    shrank to the textarea's intrinsic ~254px — narrowing the pane toward
    768px made the composer jump wider instead of staying put.
    """
    page = welcome
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    def metrics():
        return page.locator("#welcomeScreen").evaluate("""el => {
            const content = document.querySelector('.welcome-content');
            const composer = document.querySelector(
                '.welcome-input-container');
            return {
                screenScroll: el.scrollWidth,
                screenClient: el.clientWidth,
                contentScroll: content.scrollWidth,
                contentClient: content.clientWidth,
                contentWidth: content.getBoundingClientRect().width,
                composer: composer.getBoundingClientRect().width,
            };
        }""")

    # Wide pane: splash fills to its cap (old code: 254px intrinsic strip).
    page.set_viewport_size({"width": 900, "height": 800})
    wide = metrics()
    assert wide["contentWidth"] > 800, wide
    assert wide["composer"] <= 820, wide
    assert wide["composer"] > 700, wide
    # Narrow pane: everything shrinks along, nothing spills horizontally.
    page.set_viewport_size({"width": 250, "height": 800})
    narrow = metrics()
    assert narrow["contentScroll"] <= narrow["contentClient"] + 1, narrow
    assert narrow["screenScroll"] <= narrow["screenClient"] + 1, narrow
    # Very wide: the composer keeps its cap instead of full-bleed.
    page.set_viewport_size({"width": 1400, "height": 900})
    huge = metrics()
    assert huge["composer"] <= 820 + 1, huge
    assert errors == [], errors


def test_tiny_welcome_can_scroll_a_tall_draft(welcome):
    page = welcome
    page.set_viewport_size({"width": 390, "height": 180})
    # Force an overflowing draft to check that centering does not make its
    # top unreachable in a short pane.
    page.locator('#welcomeChatInput').evaluate("el => el.style.height = '260px'")
    content = page.locator('.welcome-content').bounding_box()
    screen = page.locator('#welcomeScreen').bounding_box()
    assert content['y'] >= screen['y']
    page.locator('#welcomeScreen').evaluate("el => el.scrollTop = el.scrollHeight")
    button = page.locator('#welcomeSendButton').bounding_box()
    assert button['y'] + button['height'] <= 180


def test_mobile_rail_drops_only_bottom_inset(browser, static_server):
    context = browser.new_context()
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.goto(f'{static_server}/css/safe_area.css')
    page.set_content(f'''<link rel="stylesheet" href="{static_server}/css/app_shell.css">
        <link rel="stylesheet" href="{static_server}/css/safe_area.css">
        <nav class="icon-rail"><div class="rail-footer"></div></nav>''')
    page.evaluate("""() => {
        document.documentElement.className = 'is-cuttle-mobile';
        document.documentElement.style.setProperty('--safe-area-inset-top', '24px');
        document.documentElement.style.setProperty('--safe-area-inset-bottom', '48px');
    }""")
    page.wait_for_function("getComputedStyle(document.querySelector('.rail-footer')).paddingBottom === '48px'")
    page.evaluate("document.documentElement.classList.add('keyboard-open')")
    assert page.locator('.rail-footer').evaluate("el => getComputedStyle(el).paddingBottom") == '0px'
    assert page.locator('.icon-rail').evaluate("el => getComputedStyle(el).paddingTop") == '24px'
    context.close()


def test_toast_progress_history_and_iframe_forwarding(browser, static_server):
    context = browser.new_context()
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.goto(f'{static_server}/css/safe_area.css')
    page.set_content(f'''<script src="{static_server}/js/shared/toast.js"></script>
        <iframe srcdoc='<script src="{static_server}/js/shared/toast.js"></script>'></iframe>''')
    page.wait_for_function("typeof window.showCuttleToast === 'function'")
    child = page.frames[1]
    child.wait_for_function("typeof window.showCuttleToast === 'function'")
    child.evaluate("showCuttleToast('Pushing…', 'info', {toastId: 'git-push', sticky: true, progress: true, skipHistory: true})")
    page.wait_for_function("document.querySelector('[data-toast-id=git-push]') !== null")
    assert page.evaluate('getCuttleNotificationHistory().length') == 0
    child.evaluate("showCuttleToast('Pushed main', 'success', {toastId: 'git-push'})")
    page.wait_for_function("getCuttleNotificationHistory().length === 1")
    assert page.locator('[data-toast-id=git-push]').count() == 1
    assert page.locator('[data-toast-id=git-push] .cuttle-toast-message').inner_text() == 'Pushed main'
    context.close()


def test_slash_palette_closes_across_panes(browser, static_server):
    context = browser.new_context(viewport={"width": 1000, "height": 844})
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(f"{static_server}/api/**", IsolatedAPI().handle)
    page.goto(f'{static_server}/css/safe_area.css')
    page.set_content(f'''<iframe style="width:450px;height:800px" src="{static_server}/chat_page.html"></iframe>
        <iframe style="width:450px;height:800px" srcdoc="<button>Other pane</button>"></iframe>''')
    chat = page.frames[1]
    chat.wait_for_function("typeof window.autoResizeWelcomeTextarea === 'function'")
    input = chat.locator('#welcomeChatInput')
    input.click()
    input.fill('/')
    menu = chat.locator('#welcomeSlashCommandMenu')
    menu.wait_for(state='visible')
    page.frames[2].locator('button').click()
    menu.wait_for(state='hidden')
    assert input.input_value() == '/'
    context.close()
