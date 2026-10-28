"""Notifications panel: rows open their origin, dismiss one, clear all.

Toasts fired from a chat pane carry that chat's id; toasts from any other
framed page carry its path. Clicking a row opens that chat or page, the ×
removes only that row, and Clear all empties the list.
"""
from .test_shared_diff_modal import IsolatedAPI, apply_request_guard, browser, static_server  # noqa: F401

SHELL = """<!doctype html><html><body>
<span class="rail-badge rail-badge-notifications" style="display:none">0</span>
<div class="notifications-popover" id="notificationsPopover">
  <button type="button" id="notificationsClearBtn">Clear all</button>
  <div id="notificationsPopoverList"></div>
  <div id="notificationsPopoverEmpty">No notifications yet.</div>
</div>
<div id="notificationsPopoverBackdrop"></div>
<script src="/js/shared/toast.js"></script>
<script src="/js/shell/shell_notifications.js"></script>
<script>
  window.opened = [];
  window.notif = CuttleShellNotifications.create({
    openChat: (sid) => opened.push(['chat', sid]),
    openPage: (page) => opened.push(['page', page]),
  });
</script>
<iframe src="/fake/chat_page.html?chat=42"></iframe>
<iframe src="/fake/git_graph_page.html?repo=a&msg=3"></iframe>
</body></html>"""

FRAME = '<!doctype html><script src="/js/shared/toast.js"></script>'


def test_rows_open_origin_and_dismiss(browser, static_server):
    context = browser.new_context()
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(f"{static_server}/shell.html", lambda r: r.fulfill(body=SHELL, content_type="text/html"))
    page.route(f"{static_server}/fake/**", lambda r: r.fulfill(body=FRAME, content_type="text/html"))
    page.goto(f"{static_server}/shell.html")
    chat, git = page.frames[1], page.frames[2]
    for frame in (chat, git):
        frame.wait_for_function("typeof window.showCuttleToast === 'function'")
    chat.evaluate("showCuttleToast('Copied', 'success')")
    git.evaluate("showCuttleToast('Pushed main', 'success')")
    page.evaluate("showCuttleToast('Shell only', 'info')")
    page.wait_for_function("getCuttleNotificationHistory().length === 3")

    history = page.evaluate("getCuttleNotificationHistory()")
    by_msg = {n["message"]: n for n in history}
    assert by_msg["Copied"]["sessionId"] == "42"
    assert by_msg["Copied"]["sourcePage"] is None
    assert by_msg["Pushed main"]["sessionId"] is None
    assert by_msg["Pushed main"]["sourcePage"] == "/fake/git_graph_page.html?repo=a"
    assert by_msg["Shell only"]["sourcePage"] is None

    page.evaluate("notif.open()")
    rows = page.locator(".notifications-popover-item")
    assert rows.count() == 3
    assert page.locator(".notifications-popover-item.is-clickable").count() == 2

    page.locator(".notifications-popover-item", has_text="Copied").locator(".notifications-popover-item-message").click()
    page.evaluate("notif.open()")
    page.locator(".notifications-popover-item", has_text="Pushed main").locator(".notifications-popover-item-message").click()
    assert page.evaluate("opened") == [["chat", "42"], ["page", "/fake/git_graph_page.html?repo=a"]]

    page.evaluate("notif.open()")
    page.locator(".notifications-popover-item", has_text="Shell only").locator(".notifications-popover-item-dismiss").dispatch_event("click")
    assert rows.count() == 2
    assert page.evaluate("opened.length") == 2

    page.locator("#notificationsClearBtn").click()
    assert rows.count() == 0
    assert page.locator("#notificationsPopoverEmpty").is_visible()
    assert page.locator("#notificationsClearBtn").is_disabled()
    context.close()


def test_rejects_offsite_source_page(browser, static_server):
    context = browser.new_context()
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(f"{static_server}/shell.html", lambda r: r.fulfill(body=SHELL, content_type="text/html"))
    page.route(f"{static_server}/fake/**", lambda r: r.fulfill(body=FRAME, content_type="text/html"))
    page.goto(f"{static_server}/shell.html")
    page.wait_for_function("typeof window.showCuttleToast === 'function'")
    page.evaluate("""() => {
        for (const p of ['//evil.example/x', 'javascript:alert(1)', 'https://evil.example/']) {
            window.postMessage({type: 'cuttle-toast', message: p, variant: 'info', sourcePage: p}, '*');
        }
    }""")
    page.wait_for_function("getCuttleNotificationHistory().length === 3")
    assert all(n["sourcePage"] is None for n in page.evaluate("getCuttleNotificationHistory()"))
    context.close()


def test_mobile_header_clears_status_bar(browser, static_server):
    context = browser.new_context(viewport={"width": 360, "height": 800}, is_mobile=True, has_touch=True)
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(f"{static_server}/api/**", IsolatedAPI().handle)
    page.goto(f"{static_server}/app_shell.html", wait_until="domcontentloaded")
    page.wait_for_function("typeof toggleNotificationsPopover === 'function'")
    page.evaluate("""() => {
        document.documentElement.classList.add('is-cuttle-mobile');
        document.documentElement.style.setProperty('--safe-area-inset-top', '32px');
        toggleNotificationsPopover();
    }""")
    clear = page.locator("#notificationsClearBtn")
    clear.wait_for(state="visible")
    box = clear.bounding_box()
    assert box["y"] >= 32
    assert box["x"] + box["width"] <= 360
    context.close()
