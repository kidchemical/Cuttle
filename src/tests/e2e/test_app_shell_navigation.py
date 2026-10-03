"""
E2E test for app shell navigation: nav icons, panel labels, iframe content loading.
Requires Cuttle Flask server running at http://127.0.0.1:8080.
Skips if server or Playwright not available.

Run:
  cd C:\\Projects\\Cuttle
  .venv\\Scripts\\python.exe -m pytest src/tests/e2e/test_app_shell_navigation.py -v

Start Flask first (canonical daemon or direct API module):
  ./start_cuttle.sh
  # or: .venv/bin/python src/scripts/cuttle_daemon.py
  # or: .venv/bin/python src/api/web_chat_api.py
"""
import json
import os
import sys
import uuid
import pytest
import requests
from pathlib import Path

src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

def _resolve_api_base():
    """Cuttle serves HTTPS (self-signed) on 8080; fall back to HTTP for older setups."""
    override = os.getenv("CUTTLE_API_URL")
    if override:
        return override
    for base in ("https://127.0.0.1:8080", "http://127.0.0.1:8080"):
        try:
            if requests.get(f"{base}/api/health", timeout=2, verify=False).ok:
                return base
        except requests.exceptions.RequestException:
            continue
    return "https://127.0.0.1:8080"


API_BASE = _resolve_api_base()


def _server_available():
    """Check if Cuttle API server is running."""
    try:
        r = requests.get(f"{API_BASE}/api/health", timeout=2, verify=False)
        return r.ok
    except requests.exceptions.RequestException:
        return False


def _playwright_available():
    """Check if Playwright is installed."""
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture(scope="module")
def server_available():
    return _server_available()


@pytest.fixture(scope="module")
def playwright_available():
    return _playwright_available()


@pytest.fixture(scope="module")
def browser(server_available, playwright_available):
    if not server_available:
        pytest.skip("Cuttle server not running - start with: cd electron && npm start")
    if not playwright_available:
        pytest.skip("Playwright not installed - run: pip install playwright && playwright install")
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def page(browser):
    """Fresh context per test: a warm shell keeps SSE open, so reusing one page
    makes every later goto() hang waiting for the document to settle."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    yield page
    context.close()


def _rail_footer_metrics(page):
    """Geometry of the collapse control vs the window (Electron titlebar clip)."""
    return page.evaluate(
        """() => {
            const toggle = document.querySelector('.rail-panel-toggle');
            const footer = document.querySelector('.rail-footer');
            const col = document.querySelector('.split-column');
            const vh = window.innerHeight;
            if (!toggle) return { missing: true, vh };
            const tr = toggle.getBoundingClientRect();
            const fr = footer ? footer.getBoundingClientRect() : null;
            const cr = col ? col.getBoundingClientRect() : null;
            return {
                missing: false,
                vh,
                titlebarH: Math.round((document.getElementById('shellTitlebar') || {}).offsetHeight || 0),
                toggleH: Math.round(tr.height),
                toggleBottom: tr.bottom,
                footerBottom: fr ? fr.bottom : null,
                colH: cr ? Math.round(cr.height) : null,
                clipped: tr.bottom > vh + 0.5 || (fr && fr.bottom > vh + 0.5),
            };
        }"""
    )


def test_electron_titlebar_does_not_clip_rail_footer(page):
    """Frameless Electron chrome is 36px; blade columns must fill the shell, not 100svh."""
    page.goto(f"{API_BASE}/app_shell.html", wait_until="domcontentloaded")
    page.wait_for_selector(".rail-panel-toggle", timeout=10000)
    page.evaluate(
        """() => {
            document.body.classList.add('is-electron');
            const tb = document.getElementById('shellTitlebar');
            if (tb) tb.hidden = false;
        }"""
    )
    page.wait_for_timeout(50)
    metrics = _rail_footer_metrics(page)
    assert not metrics.get("missing"), "Expected rail collapse control"
    assert metrics["titlebarH"] == 36, metrics
    assert metrics["toggleH"] >= 36, metrics
    assert metrics["clipped"] is False, metrics
    assert metrics["colH"] == metrics["vh"] - 36, metrics


def test_browser_rail_footer_stays_in_viewport(page):
    """Non-Electron (phone/desktop browser) must still keep the rail footer on-screen."""
    page.goto(f"{API_BASE}/app_shell.html", wait_until="domcontentloaded")
    page.wait_for_selector(".rail-panel-toggle", timeout=10000)
    metrics = _rail_footer_metrics(page)
    assert not metrics.get("missing"), "Expected rail collapse control"
    assert metrics["clipped"] is False, metrics


def test_workspace_icon_opens_popover(page):
    """Blade rail has a Workspace control that opens save/load (web + Electron)."""
    page.goto(f"{API_BASE}/app_shell.html", wait_until="domcontentloaded")
    btn = page.locator('[data-id="nav-workspace"]').first
    btn.wait_for(state="visible", timeout=10000)
    assert btn.get_attribute("data-tooltip") == "Workspace"
    btn.click()
    pop = page.locator("#workspacePopover")
    pop.wait_for(state="visible", timeout=5000)
    assert pop.locator("#workspaceSaveBtn").is_visible()
    assert pop.locator("#workspaceNameInput").is_visible()


def test_workspace_save_load_restores_split_and_chats(page):
    """Saved workspace restores pane count and ?chat= handles."""
    try:
        probe = requests.get(f"{API_BASE}/api/shell/workspaces", timeout=3, verify=False)
        if probe.status_code != 200:
            pytest.skip("Flask does not yet expose /api/shell/workspaces (restart required)")
    except requests.exceptions.RequestException:
        pytest.skip("Cannot reach /api/shell/workspaces")

    name = f"pytest-ws-{uuid.uuid4().hex[:8]}"
    page.goto(f"{API_BASE}/app_shell.html", wait_until="domcontentloaded")
    page.wait_for_selector("#contentFrame", timeout=10000)
    page.evaluate("() => addSplitColumn('/chat_page.html?chat=424242')")
    page.wait_for_function("() => document.querySelectorAll('.split-column').length >= 2", timeout=8000)

    page.locator('[data-id="nav-workspace"]').first.click()
    page.locator("#workspacePopover").wait_for(state="visible", timeout=5000)
    page.fill("#workspaceNameInput", name)
    page.click("#workspaceSaveBtn")
    page.locator(".workspace-item-name", has_text=name).wait_for(state="visible", timeout=8000)

    page.evaluate("() => closeSplitColumn(1)")
    page.wait_for_function("() => document.querySelectorAll('.split-column').length === 1", timeout=8000)

    item = page.locator(".workspace-item", has_text=name)
    item.locator("[data-workspace-load]").click()
    page.wait_for_function("() => document.querySelectorAll('.split-column').length >= 2", timeout=8000)
    pages = page.evaluate("() => snapshotSplitColumns().map((c) => c.page || '')")
    assert any("424242" in str(p) for p in pages), pages

    item.locator("[data-workspace-delete]").click()
    page.wait_for_timeout(400)
    # Best-effort API cleanup if the row is already gone
    try:
        listed = requests.get(f"{API_BASE}/api/shell/workspaces", timeout=3, verify=False).json()
        for ws in listed.get("workspaces") or []:
            if ws.get("name") == name:
                requests.delete(f"{API_BASE}/api/shell/workspaces/{ws['id']}", timeout=3, verify=False)
    except requests.exceptions.RequestException:
        pass


def test_nav_icons_visible(page):
    """Rail items have SVG icons (not stripped by cloneNode bug)."""
    page.goto(f"{API_BASE}/app_shell.html")
    page.wait_for_selector("#contentFrame", timeout=10000)
    # Rail items with data-page should have SVG children
    rail_svgs = page.locator(".rail-item[data-page] svg")
    count = rail_svgs.count()
    assert count >= 5, f"Expected at least 5 rail icon SVGs, got {count}"


def test_rail_items_are_labelled(page):
    """Every rail nav button carries a tooltip label (the rail replaced the old nav panel)."""
    page.goto(f"{API_BASE}/app_shell.html")
    page.wait_for_selector("#contentFrame", timeout=10000)
    labels = page.eval_on_selector_all(
        ".rail-items .rail-item[data-page]",
        "els => els.map(e => e.getAttribute('data-tooltip') || '')",
    )
    assert labels, "Rail should expose nav buttons"
    assert all(labels), f"Every rail nav button needs a tooltip, got {labels}"


def test_navigate_to_apps_and_grid_lists_apps(page):
    """Click the Apps launcher (locked on the rail); the grid lists Cuttle web apps."""
    page.goto(f"{API_BASE}/app_shell.html")
    page.wait_for_selector("#contentFrame", timeout=10000)
    page.locator('.rail-items .rail-item[data-page="/apps_page.html"]').first.click()
    frame = page.frame_locator("#contentFrame")
    frame.locator("h1").filter(has_text="Apps").wait_for(state="visible", timeout=8000)
    frame.locator('.apps-tile[data-id="nav-git"]').wait_for(state="visible", timeout=8000)


def test_rapid_navigation_panel_stable(page):
    """Rapid navigation through pages; main panel should eventually show content."""
    page.goto(f"{API_BASE}/app_shell.html")
    page.wait_for_selector("#contentFrame", timeout=10000)
    # The rail is user-customizable, so any of these may be stashed out of view.
    pages = ["/task_management.html", "/apps_page.html", "/settings_page.html", "/chat_page.html"]
    for p in pages:
        btn = page.locator(f'.rail-items .rail-item[data-page="{p}"]').first
        if btn.count() == 0 or not btn.is_visible():
            page.evaluate("(p) => navigate(0, p)", p)
        else:
            btn.click()
        page.wait_for_timeout(500)  # Allow load to start
    # Final page is chat; check iframe has some content
    frame = page.frame_locator("#contentFrame")
    # Chat page has various elements; just ensure body has content
    body = frame.locator("body")
    body.wait_for(state="visible", timeout=5000)
    assert body.count() == 1


def test_returning_to_chat_reopens_last_session(page):
    """Leaving a chat for another page and coming back re-attaches ?chat=."""
    # Chat iframes hold SSE open, so "load" never settles on a warm shell.
    page.goto(f"{API_BASE}/app_shell.html", wait_until="domcontentloaded")
    page.wait_for_selector("#contentFrame", timeout=10000)
    result = page.evaluate(
        """() => {
            rememberChatHandle(0, 'chat', '456');
            navigate(0, '/settings_page.html');
            const away = getState(0).page;
            navigate(0, '/chat_page.html');
            return { away, back: getState(0).page };
        }"""
    )
    assert result["away"] == "/settings_page.html"
    assert result["back"] == "/chat_page.html?chat=456"


def test_chat_handle_restore_respects_explicit_targets(page):
    """Deep-links, sign-in, and a fresh chat are never overridden by the remembered handle."""
    page.goto(f"{API_BASE}/app_shell.html", wait_until="domcontentloaded")
    page.wait_for_selector("#contentFrame", timeout=10000)
    result = page.evaluate(
        """() => {
            rememberChatHandle(0, 'chat', '123');
            rememberChatHandle(0, 'terminal', 'T-7');
            const r = {
                bare: withRememberedChat(0, '/chat_page.html'),
                deep: withRememberedChat(0, '/chat_page.html?chat=999'),
                signin: withRememberedChat(0, '/chat_page.html?signin=1'),
                other: withRememberedChat(0, '/settings_page.html'),
                terminal: withRememberedChat(0, '/terminal_page.html'),
            };
            // New chat reports a null handle; the splash must stay reachable.
            rememberChatHandle(0, 'chat', null);
            r.afterNewChat = withRememberedChat(0, '/chat_page.html');
            return r;
        }"""
    )
    assert result["bare"] == "/chat_page.html?chat=123"
    assert result["deep"] == "/chat_page.html?chat=999"
    assert result["signin"] == "/chat_page.html?signin=1"
    assert result["other"] == "/settings_page.html"
    assert result["terminal"] == "/terminal_page.html?chat=T-7"
    assert result["afterNewChat"] == "/chat_page.html"


def test_chat_handle_survives_shell_reload(page):
    """The remembered handle is durable, so a refresh on another page still restores it."""
    page.goto(f"{API_BASE}/app_shell.html", wait_until="domcontentloaded")
    page.wait_for_selector("#contentFrame", timeout=10000)
    page.evaluate("() => { rememberChatHandle(0, 'chat', '789'); navigate(0, '/settings_page.html'); }")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#contentFrame", timeout=10000)
    restored = page.evaluate("() => withRememberedChat(0, '/chat_page.html')")
    assert restored == "/chat_page.html?chat=789"


def test_chat_deeplink_hides_welcome_then_falls_back(page):
    """?chat= shows a spinner instead of flashing the new-chat splash, then gives up."""
    # Sample from inside the page: by the time a Playwright call lands, the restore
    # has already resolved, so an outside check can't prove the splash never flashed.
    page.add_init_script(
        """
        window.__restoreProbe = [];
        document.addEventListener('DOMContentLoaded', () => {
            window.__restoreProbe.push({
                attr: document.documentElement.getAttribute('data-chat-restoring'),
                welcome: getComputedStyle(document.getElementById('welcomeScreen')).display,
                loader: getComputedStyle(document.getElementById('chatRestoreLoader')).display,
            });
        });
        """
    )
    page.goto(f"{API_BASE}/chat_page.html?chat=99999999", wait_until="domcontentloaded")
    first = page.evaluate("() => window.__restoreProbe[0]")
    assert first["attr"] == "1"
    assert first["welcome"] == "none", "welcome splash must not flash before the transcript"
    assert first["loader"] == "flex"
    # No auth cookie and no such chat, so nothing can restore: the fallback has to
    # hand the user a new chat rather than spin forever.
    page.locator("#welcomeScreen").wait_for(state="visible", timeout=15000)
    assert page.get_attribute("html", "data-chat-restoring") is None


def test_chat_deeplink_paints_transcript_from_boot_prefetch(page):
    """A 200 from the boot prefetch paints the chat without waiting on auth.js."""
    page.route(
        "**/api/auth/sessions/424242/messages*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "success": True,
                    "messages": [
                        {"id": 1, "role": "user", "content": "restored ping"},
                        {"id": 2, "role": "assistant", "content": "restored pong"},
                    ],
                }
            ),
        ),
    )
    page.goto(f"{API_BASE}/chat_page.html?chat=424242", wait_until="domcontentloaded")
    page.locator("#chatArea").wait_for(state="visible", timeout=8000)
    assert "restored pong" in page.locator("#chatMessages").inner_text()
    assert not page.locator("#welcomeScreen").is_visible()
    assert page.get_attribute("html", "data-chat-restoring") is None


def test_tools_page_points_at_agent_ops_cli(page):
    """Tools rail lists python -m api.* verbs."""
    page.goto(f"{API_BASE}/app_shell.html")
    page.wait_for_selector("#contentFrame", timeout=10000)
    toggle = page.locator(".rail-panel-toggle")
    if toggle.is_visible():
        col = page.locator(".split-column[data-column='0']")
        if "panel-open" not in (col.get_attribute("class") or ""):
            toggle.click()
            page.wait_for_timeout(300)
    tools_btn = page.locator('[data-page="/tools_page.html"]').first
    tools_btn.click()
    page.wait_for_timeout(800)
    frame = page.frame_locator("#contentFrame")
    frame.locator("#agentOpsCli").wait_for(state="visible", timeout=10000)
    list_text = frame.locator("#agentOpsCli").inner_text()
    assert "python -m api.chat_cli" in list_text, f"Tools page should list chat_cli. Got: {list_text[:500]}"


def test_chat_page_loads_after_tools_check(page):
    """Chat page loads in app shell (verifies Cuttle is working for chat after Tools changes)."""
    page.goto(f"{API_BASE}/app_shell.html")
    page.wait_for_selector("#contentFrame", timeout=10000)
    chat_btn = page.locator('[data-page="/chat_page.html"]').first
    chat_btn.click()
    frame = page.frame_locator("#contentFrame")
    frame.locator("body").wait_for(state="visible", timeout=8000)
    # Chat page has .chat-layout and .chat-input (welcome or active)
    frame.locator(".chat-layout").wait_for(state="visible", timeout=5000)
    frame.locator(".chat-input").first.wait_for(state="visible", timeout=5000)
