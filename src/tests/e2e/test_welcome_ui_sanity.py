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


@pytest.mark.parametrize("width,height,keyboard", [
    (390, 420, True), (390, 650, True), (900, 440, False), (390, 844, False),
])
def test_welcome_centers_composer_and_clears_insets(welcome, width, height, keyboard):
    page = welcome
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("""keyboard => {
        document.documentElement.classList.add('is-cuttle-mobile');
        document.documentElement.classList.toggle('keyboard-open', keyboard);
        document.documentElement.style.setProperty('--safe-area-inset-top', '24px');
        document.documentElement.style.setProperty('--safe-area-inset-bottom', '48px');
    }""", keyboard)
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
    assert page.locator('.welcome-logo').is_visible() == (not keyboard and height > 560)
    assert page.locator('.welcome-subtitle').is_visible() == (not keyboard and height > 560)
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
