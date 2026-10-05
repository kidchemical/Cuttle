"""Production settings and completion hooks; browser/OS notifications are fakes."""
import json
import pytest
from urllib.parse import urlparse
from playwright.sync_api import expect
from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_shared_diff_modal import browser, static_server, apply_request_guard, IsolatedAPI  # noqa: F401

FAKE_NOTIFICATION = r'''
window.osNotices=[];window.permissionRequests=[];
class FakeNotification {
 static permission='default';
 static async requestPermission(){window.permissionRequests.push(navigator.userActivation.isActive);this.permission='granted';return 'granted';}
 constructor(title,options){this.title=title;this.options=options;window.osNotices.push(this);}
 close(){this.closed=true;}
}
window.Notification=FakeNotification;
void 0;
'''

class NotificationWorld(CardWorld):
    def handle(self, route):
        if urlparse(route.request.url).path == '/api/experimental/flags':
            route.fulfill(content_type='application/json', body=json.dumps({'success': True, 'flags': [{'id': 'completion_notifications', 'enabled': True}]}))
            return
        super().handle(route)


def test_settings_permission_requires_click_and_privacy_defaults(browser, static_server):
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    apply_request_guard(page.context, static_server)
    page.add_init_script(FAKE_NOTIFICATION)
    page.route(static_server + '/api/**', NotificationWorld([]).handle)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(static_server + '/settings_page.html', wait_until='domcontentloaded')
        button = page.locator('#completionNotificationsButton')
        expect(button).to_be_enabled()
        assert page.evaluate('permissionRequests') == []
        expect(page.locator('#completionNotificationsDetails')).not_to_be_checked()
        button.click()
        expect(button).to_contain_text('Turn off')
        assert page.evaluate('permissionRequests') == [True]
        expect(page.locator('#completionNotificationsStatus')).to_contain_text('at least 1 minute')
        page.locator('#notificationsToggle').click()
        expect(page.locator('#completionNotificationsStatus')).to_contain_text('paused')
        page.locator('#notificationsToggle').click()
        expect(page.locator('#completionNotificationsStatus')).to_contain_text('at least 1 minute')
        page.locator('#completionNotificationsDetails').check()
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('#completionNotificationsDetails')).to_be_checked()
        assert page.evaluate('permissionRequests') == []
        page.evaluate("Notification.permission='denied';window.cuttleCompletionSettings.refresh()")
        expect(page.locator('#completionNotificationsStatus')).to_contain_text('Permission blocked')
        page.evaluate("Notification.permission='granted';CuttleCompletionNotifications.broker().configure(true,false);window.cuttleCompletionSettings.refresh()")
        expect(page.locator('#completionNotificationsDetails')).not_to_be_checked()
        group = page.locator('.settings-group').filter(has=page.locator('#completionNotificationsButton'))
        group.screenshot(path='temp/completion-notifications-settings.png')
        page.set_viewport_size({'width':390,'height':844})
        assert page.locator('#completionNotificationsButton').evaluate('(el) => el.getBoundingClientRect().right <= window.innerWidth')
        group.screenshot(path='temp/completion-notifications-mobile.png')
        assert not errors
    finally:
        page.close()


def test_long_reply_os_delivery_and_click_destination(browser, static_server):
    class ReplyWorld(NotificationWorld):
        page = None
        def handle(self, route):
            if route.request.method == 'POST' and urlparse(route.request.url).path == '/api/chat':
                # Simulate a minute of work without waiting or invoking a harness.
                self.page.frames[1].evaluate("""() => {
                    const storeId='cuttle.completion.turn.42';
                    const rec=JSON.parse(localStorage.getItem(storeId));
                    if (!rec) throw Error('turn start was not recorded');
                    rec.startedAt-=61000;localStorage.setItem(storeId,JSON.stringify(rec));
                }""")
            super().handle(route)
    world = ReplyWorld(['Done'])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    world.page = page
    try:
        live = page.frames[1]
        live.evaluate(FAKE_NOTIFICATION)
        live.evaluate("Notification.permission='granted';CuttleCompletionNotifications.broker().configure(true)")
        frame.locator('#chatInput').fill('long request')
        frame.locator('#sendButton').click()
        expect(frame.locator('.message.assistant').last).to_contain_text('Done')
        page.wait_for_function('document.querySelector("iframe").contentWindow.osNotices.length===1')
        assert live.evaluate('osNotices[0].options.body') == 'Open the chat to view the result.'
        assert live.evaluate('permissionRequests') == []
        live.evaluate("CuttleCompletionNotifications.broker().finishTurn('42','done')")
        assert live.evaluate('osNotices.length') == 1
        # Exercise the notification's real handler while intercepting navigation.
        live.evaluate('window.openChatFromNotification = sid => window.clickedSession=sid; osNotices[0].onclick()')
        assert live.evaluate('clickedSession') == '42'
        assert not errors
    finally:
        page.close()


@pytest.mark.parametrize('terminal,cancelled,title', [
    ('done', False, 'Cuttle — Render batch finished'),
    ('failed', False, 'Cuttle — Work failed'),
    ('failed', True, 'Cuttle — Work cancelled'),
])
def test_mesh_completion_notifies_once_across_reload(browser, static_server, terminal, cancelled, title):
    from .test_chat_action_card_render import CARD_WATCH
    world = NotificationWorld([CARD_WATCH])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    state = {'state': 'running', 'percent': 20, 'label': 'Private mesh output', 'batch_id': 'batch-123'}
    page.route(static_server + '/output/x.json*', lambda r: r.fulfill(content_type='application/json', body=json.dumps(state)))
    try:
        live = page.frames[1]
        live.evaluate(FAKE_NOTIFICATION)
        live.evaluate("Notification.permission='granted';CuttleCompletionNotifications.broker().configure(true)")
        page.add_init_script(FAKE_NOTIFICATION + "Notification.permission='granted';")
        frame.locator('#chatInput').fill('start batch')
        frame.locator('#sendButton').click()
        card = frame.locator('.cuttle-action-form').last
        expect(card).to_be_visible()
        state.update(state=terminal, percent=100 if terminal == 'done' else 20, cancelled=cancelled)
        page.wait_for_function('document.querySelector("iframe").contentWindow.osNotices.length===1', timeout=10000)
        assert live.evaluate('osNotices[0].title') == title
        assert 'Private' not in live.evaluate('osNotices[0].options.body')
        page.reload(wait_until='domcontentloaded')
        expect(frame.locator('.cuttle-action-form')).to_be_visible()
        # Same durable batch event remains claimed even if history still has its running snapshot.
        live = page.frames[1]
        result = live.evaluate("""CuttleCompletionNotifications.broker().complete({
            id:'watch:/output/x.json:batch-123',sessionId:'42',kind:'mesh',outcome:'done'})""")
        assert result is False
        assert live.evaluate('osNotices.length') == 0
        assert not errors
    finally:
        page.close()
