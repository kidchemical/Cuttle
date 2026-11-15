"""Quote clips (chat_quote_clips.js), isolated in a browser with a fake page."""
from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

PAGE = '''
<div id="chatMessages" style="padding:120px 20px 20px">
  <div class="message assistant"><div class="message-content">
    <p id="p1">We should use a queue for the uploads.</p>
    <p id="p2">The cache can stay as it is.</p>
  </div></div>
</div>
<p id="outside">Text outside the transcript.</p>
<div class="chat-composer-inner" style="width:560px">
  <div id="chatAttachChipsRow" class="attach-chips-row" hidden></div>
  <textarea id="chatInput"></textarea>
  <button type="button" id="sendButton">send</button>
</div>'''

# The page's side of the contract: Send asks the owner for the composed text,
# then clears the clips with the composer.
HOST = '''(flags) => {
    window.sid = '7'; window.sent = []; window.resized = 0;
    window.clips = CuttleChatQuoteClips.create({
        doc: document, win: window, storage: localStorage,
        container: () => document.getElementById('chatMessages'), sessionId: () => window.sid,
        composers: [{ input: document.getElementById('chatInput'), before: document.getElementById('chatAttachChipsRow') }],
        onChange: () => { window.resized++; },
    });
    clips.start();
    window.flagsReady = clips.syncFlag(async () => {
        if (flags === 'fail') throw new Error('offline');
        return { json: async () => flags };
    });
    document.getElementById('sendButton').onclick = () => {
        const input = document.getElementById('chatInput');
        sent.push(clips.source(input).value); input.value = ''; clips.clear();
    };
    return window.flagsReady;
}'''

FLAGS_ON = {'success': True, 'kill_switch': False, 'flags': [{'id': 'chat_quote_clips', 'enabled': True}]}

SELECT = '''(id) => {
    const range = document.createRange();
    range.selectNodeContents(document.getElementById(id));
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
}'''


def _open(browser, static_server, flags=FLAGS_ON, width=800):
    page = browser.new_page(viewport={'width': width, 'height': 520})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(static_server + '/agent_feed.html')
    page.evaluate('localStorage.clear()')
    page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                     '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">' + PAGE)
    page.add_script_tag(url=static_server + '/js/chat/chat_quote_clips.js')
    page.evaluate(HOST, flags)
    return page, errors


def test_quick_reply_stages_removable_clips_and_sends_them(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        toolbar, cards = page.locator('.quote-toolbar'), page.locator('.quote-clip')
        expect(toolbar).to_have_count(0)
        resized = page.evaluate('resized')
        page.evaluate(SELECT, 'p1')
        expect(toolbar).to_be_visible()
        expect(toolbar.locator('button')).to_have_text(
            ['Reply', 'Agree', 'Disagree', 'Explain', 'Investigate', 'Do this'])
        expect(toolbar.locator('.quote-toolbar-more')).to_have_count(0)  # all fit at 800px
        # The toolbar sits above the selected text.
        assert toolbar.bounding_box()['y'] + toolbar.bounding_box()['height'] <= page.locator('#p1').bounding_box()['y']

        toolbar.locator('[data-action="agree"]').click()
        expect(toolbar).to_have_count(0)
        expect(cards).to_have_count(1)
        expect(cards.locator('.quote-clip-reply')).to_have_text('👍 Agree')
        expect(cards.locator('.quote-clip-quote')).to_have_text('We should use a queue for the uploads.')
        assert page.evaluate('window.getSelection().isCollapsed')

        # Several clips; each has its own remove button.
        for action in ('explain', 'investigate'):
            page.evaluate(SELECT, 'p2')
            toolbar.locator(f'[data-action="{action}"]').click()
        expect(cards).to_have_count(3)
        cards.nth(1).locator('.quote-clip-remove').click()
        expect(cards).to_have_count(2)
        expect(cards.locator('.quote-clip-reply')).to_have_text(['👍 Agree', '🔍 Investigate'])
        # The page is told each time the clip row changes height.
        assert page.evaluate('resized') - resized == 4

        # Clips survive a reload of the same chat, and belong to that chat only.
        assert len(page.evaluate("JSON.parse(localStorage.getItem('cuttle.quoteClips.7'))")) == 2
        page.evaluate("() => { window.sid = '8'; clips.render(); }")
        expect(cards).to_have_count(0)
        page.evaluate("() => { window.sid = '7'; clips.render(); }")
        expect(cards).to_have_count(2)

        page.fill('#chatInput', 'then tell me')
        page.click('#sendButton')
        assert page.evaluate('sent') == [
            '> We should use a queue for the uploads.\n\nI agree with this.\n\n'
            '> The cache can stay as it is.\n\nInvestigate this further and report what you find.\n\n'
            'then tell me']
        expect(cards).to_have_count(0)
        expect(page.locator('.quote-clips-row')).to_be_hidden()
        assert page.evaluate("localStorage.getItem('cuttle.quoteClips.7')") is None
        assert errors == []
    finally:
        page.close()


def test_reply_panel_adds_a_typed_clip(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        toolbar, panel, cards = page.locator('.quote-toolbar'), page.locator('.quote-reply-panel'), page.locator('.quote-clip')
        page.evaluate(SELECT, 'p2')
        toolbar.locator('[data-action="reply"]').click()
        expect(toolbar).to_have_count(0)
        expect(panel).to_be_visible()
        expect(panel.locator('.quote-reply-quote')).to_have_text('The cache can stay as it is.')
        expect(panel.locator('.quote-reply-input')).to_be_focused()
        accept = panel.locator('.quote-reply-accept')
        expect(accept).to_be_disabled()

        # Cancel adds nothing.
        panel.locator('.quote-reply-cancel').click()
        expect(panel).to_have_count(0)
        expect(cards).to_have_count(0)

        page.evaluate(SELECT, 'p2')
        toolbar.locator('[data-action="reply"]').click()
        panel.locator('.quote-reply-input').fill('It serves stale rows after a rename.')
        # A typed reply survives a click elsewhere.
        page.locator('#outside').click()
        expect(panel).to_be_visible()
        accept.click()
        expect(panel).to_have_count(0)
        expect(cards.locator('.quote-clip-reply')).to_have_text('↩ It serves stale rows after a rename.')

        page.click('#sendButton')
        assert page.evaluate('sent') == ['> The cache can stay as it is.\n\nIt serves stale rows after a rename.']

        # Enter accepts, Escape cancels.
        page.evaluate(SELECT, 'p1')
        toolbar.locator('[data-action="reply"]').click()
        panel.locator('.quote-reply-input').fill('yes')
        page.keyboard.press('Enter')
        expect(cards).to_have_count(1)
        page.evaluate(SELECT, 'p1')
        toolbar.locator('[data-action="reply"]').click()
        page.keyboard.press('Escape')
        expect(panel).to_have_count(0)
        expect(cards).to_have_count(1)
        assert errors == []
    finally:
        page.close()


def test_toolbar_only_for_transcript_selections(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        toolbar = page.locator('.quote-toolbar')
        page.evaluate(SELECT, 'outside')
        page.wait_for_timeout(300)
        expect(toolbar).to_have_count(0)
        page.evaluate(SELECT, 'p1')
        expect(toolbar).to_be_visible()
        # Clicking away or pressing Escape dismisses it.
        page.locator('#outside').click()
        expect(toolbar).to_have_count(0)
        page.evaluate(SELECT, 'p1')
        expect(toolbar).to_be_visible()
        page.keyboard.press('Escape')
        expect(toolbar).to_have_count(0)
        assert errors == []
    finally:
        page.close()


def test_narrow_toolbar_moves_extra_buttons_under_more(browser, static_server):
    page, errors = _open(browser, static_server, width=360)
    try:
        toolbar = page.locator('.quote-toolbar')
        page.evaluate(SELECT, 'p1')
        expect(toolbar).to_be_visible()
        box = toolbar.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= 360  # one capsule, inside the screen
        assert page.evaluate("document.querySelector('.quote-toolbar').scrollWidth"
                             " <= document.querySelector('.quote-toolbar').clientWidth")
        more, menu = toolbar.locator('.quote-toolbar-more'), toolbar.locator('.quote-toolbar-menu')
        expect(toolbar.locator(':scope > [data-action="reply"]')).to_be_visible()  # Reply never overflows
        expect(menu).to_be_hidden()
        more.click()
        expect(menu).to_be_visible()
        expect(menu.locator('[data-action="do"]')).to_be_visible()
        menu.locator('[data-action="do"]').click()
        expect(toolbar).to_have_count(0)
        expect(page.locator('.quote-clip')).to_have_count(1)
        assert errors == []
    finally:
        page.close()


def test_flag_off_hides_everything_and_keeps_saved_clips(browser, static_server):
    off = {'success': True, 'kill_switch': False, 'flags': [{'id': 'chat_quote_clips', 'enabled': False}]}
    kill = dict(FLAGS_ON, kill_switch=True)
    for flags in (off, kill, 'fail'):
        page, errors = _open(browser, static_server, flags)
        try:
            page.evaluate("localStorage.setItem('cuttle.quoteClips.7', JSON.stringify("
                          "[{action: 'agree', quote: 'q', reply: 'I agree with this.'}]))")
            page.evaluate('clips.render()')
            page.evaluate(SELECT, 'p1')
            page.wait_for_timeout(300)
            expect(page.locator('.quote-toolbar')).to_have_count(0)
            expect(page.locator('.quote-clip')).to_have_count(0)
            page.fill('#chatInput', 'plain')
            assert page.evaluate("clips.source(document.getElementById('chatInput')).value") == 'plain'
            # Turning it on in the page brings the saved clip back.
            page.evaluate('clips.setEnabled(true)')
            expect(page.locator('.quote-clip')).to_have_count(1)
            page.evaluate(SELECT, 'p2')
            expect(page.locator('.quote-toolbar')).to_be_visible()
            page.evaluate('clips.setEnabled(false)')
            expect(page.locator('.quote-toolbar')).to_have_count(0)
            expect(page.locator('.quote-clip')).to_have_count(0)
            assert errors == []
        finally:
            page.close()
