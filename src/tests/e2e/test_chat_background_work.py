"""Chat background-work bubble (chat_background_work.js), isolated in a browser."""
import os
import json
import re

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

LIVE = {'success': True, 'generating': False, 'active': False, 'background': {
    'label': 'Waiting on: Wait up to 40 min for fg12', 'process_count': 10, 'detached_count': 1,
    'waits': [{'job_id': 'claude-bg-1821-x', 'label': 'Waiting on: fg12', 'started_at': None}],
    'watches': [{'job_id': 'build', 'label': 'Unity build', 'percent': 40}],
    'schedules': [], 'started_at': None, 'working': True, 'feed': True}}


def test_bubble_stays_last_hides_while_generating_and_stops(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    cancels = []

    def on_cancel(route):
        cancels.append(json.loads(route.request.post_data or '{}'))
        route.fulfill(content_type='application/json', body=json.dumps({'success': True}))

    page.route('**/api/chat-cancel', on_cancel)
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">reply</div></div>')
        page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate('''() => {
            window.busy = false; window.open = '1821';
            window.bw = CuttleChatBackgroundWork.create({ win: window, currentSession: () => window.open,
                sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => window.busy });
        }''')
        page.evaluate('live => bw.sync(live, "1821")', LIVE)
        bubble = page.locator('#chat-background-work')
        expect(bubble).to_be_visible()
        expect(bubble.locator('.chat-bg-title')).to_contain_text('Waiting in background')
        expect(bubble.locator('.chat-bg-label')).to_have_text('Waiting on: Wait up to 40 min for fg12')
        expect(bubble.locator('.chat-bg-meta')).to_have_text('10 processes (1 detached)')
        expect(bubble.locator('.chat-bg-pct')).to_have_text('40%')
        assert bubble.locator('.chat-bg-details').get_attribute('href') == '/agent_feed.html?chat=CH-001821'
        # Not a transcript record, and it stays last when a follow-up bubble lands.
        assert page.locator('#chatMessages .message').count() == 1
        page.evaluate('''() => { const m = document.createElement('div'); m.className = 'message assistant';
            m.textContent = 'follow-up'; document.getElementById('chatMessages').appendChild(m); }''')
        page.wait_for_function("document.getElementById('chatMessages').lastElementChild.id === 'chat-background-work'")
        # A missed poll keeps it; another chat's status is ignored; a turn hides it.
        page.evaluate('() => bw.sync(null, "1821")')
        expect(bubble).to_be_visible()
        page.evaluate('live => bw.sync({ ...live, background: null }, "999")', LIVE)
        expect(bubble).to_be_visible()
        page.evaluate('live => bw.sync({ ...live, generating: true }, "1821")', LIVE)
        expect(bubble).to_have_count(0)
        page.evaluate('live => bw.sync(live, "1821")', LIVE)
        page.locator('.chat-bg-stop').click()
        expect(page.locator('.chat-bg-stop')).to_have_text('Stopped')
        assert cancels == [{'session_id': '1821', 'cancel_jobs': True}]
        page.evaluate('live => bw.sync({ ...live, background: null }, "1821")', LIVE)
        expect(bubble).to_have_count(0)
        assert not errors
    finally:
        page.close()


SCHEDULED = {'success': True, 'generating': False, 'active': False, 'background': {
    'label': 'Supervise CH-001821 · every 15m', 'process_count': 0, 'detached_count': 0,
    'waits': [], 'watches': [], 'started_at': None, 'working': False, 'feed': False,
    'schedules': [{'id': 'op1', 'label': 'Supervise CH-001821', 'detail': 'every 15m',
                   'status': 'running', 'target': 'CH-001821'}]}}


def test_card_keeps_its_height_in_a_crowded_transcript(browser, static_server):
    page = browser.new_page(viewport={'width': 390, 'height': 500})
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages" class="chat-messages" style="height:200px">'
                         '<div style="flex:none;height:600px">Long transcript</div></div>')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate('''() => { window.bw = CuttleChatBackgroundWork.create({win: window,
            currentSession: () => '1822', sessionIdsEqual: (a, b) => a === b, isBusy: () => false}); }''')
        page.evaluate('live => bw.sync(live, "1822")', SCHEDULED)
        card = page.locator('#chat-background-work')
        head = card.locator('.chat-bg-head')
        expect(head).to_be_visible()
        sizes = card.evaluate('(el) => ({card: el.getBoundingClientRect().height, head: el.firstElementChild.getBoundingClientRect().height})')
        assert sizes['head'] > 20
        assert sizes['card'] >= sizes['head']
        # The old default flex shrink collapses this same card to its border.
        collapsed = card.evaluate('''el => {
            el.style.flexShrink = '1';
            return el.getBoundingClientRect().height;
        }''')
        assert collapsed < sizes['head']
    finally:
        page.close()


def test_loose_process_count_is_shown_once(browser, static_server):
    page = browser.new_page()
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<div id="chatMessages"></div>')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate("""() => { window.bw = CuttleChatBackgroundWork.create({win:window,
            currentSession:()=> '7', sessionIdsEqual:(a,b)=>a===b, isBusy:()=>false}); }""")
        for n, d, title in [(1, 1, '1 detached process running'), (2, 2, '2 detached processes running'),
                            (3, 1, '3 processes running (1 detached)'), (1, 0, '1 process running')]:
            page.evaluate('bg => bw.sync({background:bg}, "7")', {
                'label': f'{n} {"process" if n == 1 else "processes"} still running' + (f' ({d} detached)' if d else ''),
                'process_count': n, 'detached_count': d, 'started_at': None})
            card = page.locator('#chat-background-work')
            expect(card.locator('.chat-bg-title')).to_have_text(title)
            expect(card.locator('.chat-bg-label')).to_have_count(0)
            expect(card.locator('.chat-bg-meta')).to_have_count(0)
        # A useful description survives; only the generated count is redundant.
        page.evaluate('() => bw.sync({background:{label:"Building the laptop player",process_count:1}}, "7")')
        expect(page.locator('.chat-bg-label')).to_have_text('Building the laptop player')
        # Running processes are "working": the busy mark, not a supervision or idle one.
        expect(page.locator('#chat-background-work')).to_have_attribute('data-state', 'working')
    finally:
        page.close()


def test_supervising_card_opens_the_supervised_chat(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">reply</div></div>')
        page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate('''() => {
            window.opened = [];
            window.bw = CuttleChatBackgroundWork.create({ win: window, currentSession: () => '1822',
                sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => false,
                openChat: (sid) => window.opened.push(sid) });
        }''')
        page.evaluate('live => bw.sync(live, "1822")', SCHEDULED)
        card = page.locator('#chat-background-work')
        expect(card.locator('.chat-bg-title')).to_contain_text('Supervising')
        expect(card.locator('.chat-bg-label')).to_have_count(0)
        # One line: the chat it watches as a chip, the interval as a chip, no prose.
        expect(card.locator('.chat-bg-open')).to_have_text('CH-001821')
        expect(card.locator('.chat-bg-row-aside .chat-bg-chip')).to_have_text('every 15m')
        expect(card.locator('.chat-bg-rows')).to_have_count(0)
        expect(card.locator('.chat-bg-supervising')).to_have_count(0)
        expect(card).to_have_class(re.compile(r'\bis-clickable\b'))
        # Active supervision is alive (green ping), not a dimmed still ring.
        expect(card).to_have_attribute('data-state', 'supervising')
        assert card.locator('.chat-bg-ring').evaluate(
            "e => getComputedStyle(e, '::before').animationName") == 'chat-bg-ping'
        card.screenshot(path=os.environ.get('BG_CARD_SHOT_SUPERVISING', '/tmp/bg_supervising.png'))
        card.locator('.chat-bg-open').click()
        card.locator('.chat-bg-title').click()
        assert page.evaluate('() => window.opened') == ['1821', '1821']
        paused = json.loads(json.dumps(SCHEDULED))
        paused['background']['schedules'][0].update(status='paused', detail='every 15m · last seen waiting_background')
        paused['background']['label'] = 'Supervise CH-001821 · every 15m · last seen waiting_background'
        page.evaluate('live => bw.sync(live, "1822")', paused)
        expect(card.locator('.chat-bg-title')).to_have_text('Supervision paused')
        expect(card).to_have_attribute('data-state', 'paused')
        assert card.locator('.chat-bg-ring').evaluate(
            "e => getComputedStyle(e, '::before').animationName") == 'none'
        card.screenshot(path=os.environ.get('BG_CARD_SHOT_PAUSED', '/tmp/bg_paused.png'))
        assert card.locator('.chat-bg-seen').get_attribute('title') == 'No check-ins until resumed'
        expect(card.locator('.chat-bg-label')).to_have_count(0)
        expect(card.locator('.chat-bg-supervising')).to_have_count(0)
        expect(card.locator('.chat-bg-open')).to_have_text('CH-001821')
        # Running again, with the structured facts the host sends: last seen state
        # in two words and a ring counting down to the next check.
        paused['background']['schedules'][0].update(status='running', kind='supervise', every=900,
                                                    seen='waiting_background',
                                                    next_at=page.evaluate('() => Date.now() / 1000 + 450'))
        page.evaluate('live => bw.sync(live, "1822")', paused)
        expect(card.locator('.chat-bg-seen')).to_have_text('waiting on a job')
        expect(card.locator('.chat-bg-next-text')).to_have_text('7m')
        assert card.locator('.chat-bg-next').get_attribute('title') == 'Next check in 7m'
        # A send that has waited past the stuck threshold is flagged, with its age.
        paused['background']['schedules'].append({
            'id': 'op2', 'kind': 'send', 'label': 'Send → CH-002365', 'detail': 'Recheck please', 'status': 'queued',
            'target': 'CH-002365', 'waiting_since': page.evaluate('() => Date.now() / 1000 - 4 * 3600 - 40 * 60')})
        page.evaluate('live => bw.sync(live, "1822")', paused)
        send = card.locator('.chat-bg-send')
        expect(send).to_have_class(re.compile(r'\bis-warn\b'))
        expect(send.locator('.chat-bg-age')).to_have_text('queued 4h 40m')
        expect(send.locator('.chat-bg-open')).to_have_text('CH-002365')
        assert not errors
    finally:
        page.close()


def test_detached_unit_watch_shows_unit_state_and_elapsed(browser, static_server):
    """CH-001821's laptop build, as the chat shows it: "Running: ch1821-rig-build · running · 14m"."""
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">reply</div></div>')
        page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate("""() => {
            window.bw = CuttleChatBackgroundWork.create({ win: window, currentSession: () => '1821',
                sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => false });
        }""")
        started = page.evaluate('() => Date.now() / 1000 - 14 * 60 - 5')
        live = {'success': True, 'generating': False, 'active': False, 'background': {
            'label': 'Running: ch1821-rig-build', 'process_count': 0, 'detached_count': 0, 'waits': [],
            'watches': [{'job_id': 'unit-ch1821-rig-build-ab12cd', 'kind': 'unit', 'label': 'Running: ch1821-rig-build',
                         'unit': 'ch1821-rig-build', 'sub_state': 'running', 'percent': None, 'started_at': started}],
            'schedules': [], 'started_at': None, 'working': True, 'feed': False}}
        page.evaluate('live => bw.sync(live, "1821")', live)
        card = page.locator('#chat-background-work')
        expect(card.locator('.chat-bg-title')).to_contain_text('Background job running')
        expect(card.locator('.chat-bg-head .chat-bg-elapsed')).to_have_text('14m')
        row = card.locator('summary.chat-bg-watch')
        expect(row.locator('.chat-bg-watch-label')).to_have_text('Running: ch1821-rig-build')
        assert row.locator('.chat-bg-watch-state').count() == 0  # technical state lives in Details
        assert row.locator('.chat-bg-watch-elapsed').count() == 0  # elapsed already in title
        assert row.locator('.chat-bg-watch-unit').count() == 0  # already in the label
        # No measured progress: an animated indeterminate bar, never a "0%".
        assert row.locator('.chat-bg-bar[data-progress="indeterminate"]').count() == 1
        assert row.locator('.chat-bg-pct').count() == 0
        assert card.locator('.chat-bg-stop').count() == 0  # its watch card stops it, not chat Stop
        assert card.locator('.chat-bg-label').count() == 0  # no duplicate of the row label
        # The watch fired (record gone): the card clears.
        page.evaluate('live => bw.sync({ ...live, background: null }, "1821")', live)
        expect(card).to_have_count(0)
        assert not errors
    finally:
        page.close()


def test_timeline_notice_running_settles_into_saved_row(browser, static_server):
    """Compact flow: running row spins, settles in place, persists to its own chat."""
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    posts = []

    def on_post(route):
        posts.append((route.request.url, json.loads(route.request.post_data or '{}')))
        route.fulfill(content_type='application/json', body=json.dumps({'success': True, 'message_id': 77}))

    page.route('**/api/auth/sessions/*/messages', on_post)
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message user">hi</div></div>')
        page.add_script_tag(url=static_server + '/js/chat/chat_timeline_notice.js')
        page.evaluate('''() => {
            window.sid = '1947'; window.seen = 0;
            window.tn = CuttleChatTimelineNotice.create({ doc: document, win: window,
                escapeHtml: (s) => String(s).replace(/</g, '&lt;'), fetch: (u, o) => fetch(u, o),
                container: () => document.getElementById('chatMessages'), isVisible: () => true,
                autoScroll: () => {}, persistTarget: () => window.sid, onPersisted: (m) => { window.seen = m; } });
            window.row = tn.add({ type: 'compact', state: 'running', label: 'Compacting Claude context…' },
                { kind: 'context-compact', replace: false });
        }''')
        notice = page.locator('.message.system[data-notice-type="compact"] .timeline-notice')
        expect(notice).to_have_class(re.compile('is-running'))
        expect(notice.locator('.timeline-notice-spinner')).to_have_count(1)
        expect(notice.locator('.timeline-notice-elapsed')).to_have_text(re.compile(r'^\ds$'), timeout=3000)
        page.wait_for_timeout(3300)  # running rows never auto-fade
        expect(notice).to_have_count(1)
        assert posts == []  # running rows are never persisted
        # The user switched chats while compaction ran: the row still saves to 1947.
        page.evaluate('''() => { window.sid = '2000';
            tn.update(row, { type: 'compact', label: 'Claude context compacted', detail: '82% → 14%' },
                { persist: true, kind: 'context-compact' }); }''')
        expect(notice).to_have_class(re.compile('is-done'))
        expect(notice).to_have_text('🗜️ Claude context compacted · 82% → 14%'.replace(' · ', ' '))
        page.wait_for_function('window.seen === 77')
        url, body = posts[0]
        assert url.endswith('/api/auth/sessions/1947/messages')
        assert body['content'] == '🗜️ Claude context compacted · 82% → 14%'
        assert body['kind'] == 'context-compact'
        assert body['metadata']['timeline_notice']['detail'] == '82% → 14%'
        # A detached row is not repainted or persisted into whatever chat is open now.
        assert page.evaluate('''() => { const el = tn.add({ type: 'compact', state: 'running', label: 'x' });
            el.remove(); return tn.update(el, { label: 'y' }, { persist: true }); }''') is None
        assert len(posts) == 1
        assert not errors
    finally:
        page.close()


def test_watch_rows_fit_the_card_on_a_phone(browser, static_server):
    """Narrow screen: a long unit name and a 110px progress bar never widen a row past the card."""
    page = browser.new_page(viewport={'width': 360, 'height': 700})
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">reply</div></div>')
        page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate("""() => {
            window.bw = CuttleChatBackgroundWork.create({ win: window, currentSession: () => '1821',
                sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => false });
        }""")
        live = {'success': True, 'generating': False, 'active': False, 'background': {
            'label': 'Unity build', 'process_count': 0, 'detached_count': 0, 'waits': [],
            'watches': [{'job_id': 'b', 'label': 'Unity build for the laptop playtest', 'percent': 40},
                        {'job_id': 'u', 'kind': 'unit', 'label': 'Laptop build',
                         'unit': 'ch1821-rig-build-windows-il2cpp-development-player', 'sub_state': 'running',
                         'percent': None, 'started_at': 1000}],
            'schedules': [], 'started_at': None, 'working': True, 'feed': True}}
        page.evaluate('live => bw.sync(live, "1821")', live)
        page.wait_for_selector('#chat-background-work .chat-bg-watch')
        boxes = page.evaluate("""() => {
            const card = document.getElementById('chat-background-work').getBoundingClientRect();
            const body = document.querySelector('#chat-background-work .chat-bg-rows').getBoundingClientRect();
            return { card: [card.left, card.right], body: [body.left, body.right],
                     rows: [...document.querySelectorAll('#chat-background-work .chat-bg-watch')].map((r) => {
                         const b = r.getBoundingClientRect();
                         return { left: b.left, right: b.right, scroll: r.scrollWidth, client: r.clientWidth };
                     }),
                     bar: document.querySelector('.chat-bg-bar').getBoundingClientRect().width };
        }""")
        assert boxes['card'][1] <= 360
        for row in boxes['rows']:
            assert row['right'] <= boxes['body'][1] + 0.5 and row['right'] <= boxes['card'][1]
            assert row['scroll'] <= row['client'] + 1  # nothing overflows inside the row
        assert boxes['bar'] >= 36
    finally:
        page.close()


def test_live_cards_ride_only_their_own_turns_bubble(browser, static_server):
    """A turn's cards ride that turn's live bubble, never another turn's; the
    saved reply then carries them once (hand-over, no copy)."""
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"></div>')
        page.add_script_tag(url=static_server + '/js/chat/chat_subagent_fleet.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_subagent_launchers.js')
        page.evaluate("""() => {
            window.L = CuttleChatSubagentLaunchers.create({ win: window,
                escapeHtmlInline: (s) => String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
                parseChatHandleToken: (t) => { const m = String(t).match(/^CH-0*(\\d+)$/); return m ? { display: t, sessionId: m[1] } : null; },
                avatarHtml: () => '', chipCategoryClass: () => '' });
            window.card = (bound) => ({ handle: 'CH-000011', label: 'Scout', agent: 'codex', fleet: true,
                outcome: 'running', summary: 'Working…', bound_message_id: bound, turn_id: 'turn40' });
            window.copies = () => document.querySelectorAll('[data-chat-handle="CH-000011"]').length;
            const t = document.createElement('div'); t.id = 'typing-indicator'; t.className = 'message assistant';
            t.dataset.turnMessageId = '40'; t.dataset.turnId = 'turn40';
            t.innerHTML = '<div class="message-content-wrapper"><div class="message-content">…</div></div>';
            document.getElementById('chatMessages').appendChild(t);
            L.syncLive([card(40)]);
        }""")
        expect(page.locator('#typing-indicator .subagent-fleet-card')).to_have_count(1)
        # A live bubble of ANOTHER turn never takes this turn's cards.
        page.evaluate("() => { document.getElementById('typing-indicator').dataset.turnId = 'other'; L.syncLive([card(40)]); }")
        expect(page.locator('#typing-indicator .subagent-fleet-card')).to_have_count(0)
        assert page.locator('#subagent-live-strip').count() == 0  # the pinned card shows them instead
        # Back on its own turn's bubble, then the reply lands in row 40 carrying them.
        page.evaluate("""() => {
            const t = document.getElementById('typing-indicator'); t.dataset.turnId = 'turn40'; L.syncLive([card(40)]);
            t.removeAttribute('id'); t.dataset.messageId = '40'; delete t.dataset.turnMessageId;
            L.syncLive([card(40)]);
            L.mount(t, [card(40)]);
        }""")
        assert page.evaluate('() => copies()') == 1
        assert not errors
    finally:
        page.close()


def test_subagents_are_left_to_their_transcript_cards(browser, static_server):
    """CH-001822 / CH-001821: a parent waits on `python -m api.subagents spawn --wait`,
    either as a harness background task or a detached systemd unit. The children
    already have transcript cards, so the pinned card lists neither the children
    nor the run that waits on them; only other work (here: supervision) stays."""
    page = browser.new_page(viewport={'width': 360, 'height': 740})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">reply</div></div>')
        page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate("""() => {
            window.bw = CuttleChatBackgroundWork.create({ win: window, currentSession: () => '1822',
                sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => false });
        }""")
        spawn_wait = 'Waiting on: python -m api.subagents spawn --parent CH-001822 --wait'
        unit = {'job_id': 'ch1822-p9l5', 'label': 'Bots P9 L5 lane', 'kind': 'unit', 'unit': 'ch1822-p9l5',
                'sub_state': 'running', 'state': 'running', 'handoff': 'on_exit', 'subagents': True,
                'resume_chat': 'CH-001822', 'resume_note': 'L5 lane exited: read p9l5-report.md'}
        kids = [{'handle': 'CH-002111', 'session_id': 2111, 'label': 'Cuttle fixes', 'agent': 'claude',
                 'fleet': True, 'outcome': 'running', 'summary': 'Running pytest'},
                {'handle': 'CH-002073', 'session_id': 2073, 'label': 'Reviewer', 'agent': 'codex',
                 'fleet': True, 'outcome': 'done', 'summary': 'Approved.'}]
        live = {'success': True, 'generating': False, 'active': False,
                'background': {'label': spawn_wait, 'process_count': 0, 'detached_count': 0,
                               'started_at': None, 'working': True, 'watches': [unit], 'feed': False,
                               'waits': [{'job_id': 'claude-bg-1822-x', 'label': spawn_wait, 'started_at': None}],
                               'schedules': [{'id': 'op1', 'label': 'Supervise CH-001821', 'detail': 'every 15m',
                                              'status': 'running', 'target': 'CH-001821'}]},
                'subagents': kids}
        page.evaluate('live => bw.sync(live, "1822")', live)
        card = page.locator('#chat-background-work')
        expect(card.locator('.chat-bg-title')).to_have_text('Supervising')
        expect(card).not_to_contain_text('Cuttle fixes')
        expect(card).not_to_contain_text('Bots P9 L5')
        expect(card).not_to_contain_text('api.subagents')
        assert card.locator('.chat-bg-open').all_inner_texts() == ['CH-001821']
        assert card.locator('.chat-bg-stop').count() == 0  # nothing visible to stop
        # Only the sub-agent run itself: no card at all.
        only = {**live, 'background': {**live['background'], 'schedules': []}}
        page.evaluate('live => bw.sync(live, "1822")', only)
        expect(card).to_have_count(0)
        # Children finished but the detached unit still runs: it is ordinary work again.
        done = {**only, 'subagents': [{**k, 'outcome': 'done'} for k in kids]}
        page.evaluate('live => bw.sync(live, "1822")', done)
        expect(card.locator('.chat-bg-watch-label')).to_have_text('Bots P9 L5 lane')
        expect(card).not_to_contain_text('L5 lane exited')
        assert not errors
    finally:
        page.close()


def test_indeterminate_bars_animate_and_respect_reduced_motion(browser, static_server):
    """A watch with no measured progress slides a stripe (no "0%", elapsed shown);
    a measured one is a plain fill + percent; reduced motion gets a static striped bar."""
    for reduced in (False, True):
        context = browser.new_context(reduced_motion='reduce' if reduced else 'no-preference',
                                      viewport={'width': 900, 'height': 700})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        try:
            page.goto(static_server + '/agent_feed.html')
            page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                             '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                             '<div id="chatMessages"><div class="message assistant">reply</div></div>')
            page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
            page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
            page.evaluate("""() => { window.bw = CuttleChatBackgroundWork.create({ win: window,
                currentSession: () => '7', sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => false }); }""")
            started = page.evaluate('() => Date.now() / 1000 - 125')
            live = {'success': True, 'generating': False, 'active': False, 'background': {
                'label': 'Upload', 'process_count': 0, 'detached_count': 0, 'waits': [], 'schedules': [],
                'started_at': None, 'working': True, 'feed': False, 'watches': [
                    {'job_id': 'u', 'label': 'Upload', 'percent': None, 'started_at': started},
                    {'job_id': 'b', 'label': 'Build', 'percent': 40, 'started_at': started}]}}
            page.evaluate('live => bw.sync(live, "7")', live)
            rows = page.locator('.chat-bg-watch')
            expect(rows).to_have_count(2)
            unknown, measured = rows.nth(0), rows.nth(1)
            assert unknown.locator('.chat-bg-pct').count() == 0
            expect(unknown.locator('.chat-bg-watch-elapsed')).to_have_text('2m')
            fill = unknown.locator('.chat-bg-bar[data-progress="indeterminate"] .chat-bg-fill')
            style = fill.evaluate("""el => { const c = getComputedStyle(el);
                return { name: c.animationName, image: c.backgroundImage, width: el.getBoundingClientRect().width,
                         track: el.parentElement.getBoundingClientRect().width }; }""")
            if reduced:
                assert style['name'] == 'none' and 'repeating-linear-gradient' in style['image']
                assert abs(style['width'] - style['track']) < 1  # static stripes across the track
            else:
                assert style['name'] == 'cuttle-progress-slide'
                assert 0 < style['width'] < style['track']  # a sliding stripe, not a fill
            expect(measured.locator('.chat-bg-pct')).to_have_text('40%')
            assert measured.locator('[data-progress]').count() == 0
            assert not errors
        finally:
            context.close()


def test_job_steps_details_staleness_and_completion_on_phone(browser, static_server):
    """Runner facts, escaped output, persistent disclosure and terminal delivery receipts."""
    context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                         '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">The laptop tests are running.</div></div>')
        page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate("""() => { window.bw = CuttleChatBackgroundWork.create({ win: window,
            currentSession: () => '7', sessionIdsEqual: (a,b) => String(a) === String(b), isBusy: () => false }); }""")
        now = page.evaluate('Date.now() / 1000')
        job = {'job_id': 'laptop', 'label': 'Laptop build and multiplayer checks', 'kind': 'unit',
               'unit': 'ch7-tests', 'state': 'running', 'handoff': 'on_exit', 'resume_chat': 'CH-000007',
               'origin_query_id': 'origin7', 'resume_note': 'Read the case logs and report the failures.',
               'started_at': now - 7200, 'activity': {'schema_version': 1, 'updated_ts': now - 900,
               'stale_after_sec': 600, 'output': '<script>window.injected = true</script>\nCase failed.',
               'steps': [{'id': 'build', 'label': 'Build laptop player', 'state': 'done'},
                         {'id': 'solo', 'label': 'Solo monster playthrough', 'state': 'failed', 'detail': 'Menu focus assertion failed'},
                         {'id': 'presence', 'label': 'Check player facing', 'state': 'running'},
                         {'id': 'menu', 'label': 'Check menu at five window shapes', 'state': 'pending'}]}}
        bg = {'watches': [job], 'waits': [], 'schedules': [], 'working': True}
        page.evaluate('bg => bw.sync({background: bg}, "7")', bg)
        card = page.locator('#chat-background-work')
        # Closed, the row says it with graphics: one pip per step, a count, the
        # step running now, and a "quiet" chip instead of a sentence.
        row = card.locator('summary.chat-bg-watch')
        expect(row.locator('.chat-bg-pip')).to_have_count(4)
        assert [row.locator('.chat-bg-pip').nth(i).get_attribute('class').split('is-')[1] for i in range(4)] == [
            'done', 'failed', 'running', 'pending']
        expect(row.locator('.chat-bg-count')).to_have_text('2/4')
        expect(row.locator('.chat-bg-now')).to_have_text('Check player facing')
        expect(row.locator('.chat-bg-chip.is-warn')).to_have_text('quiet')
        expect(card.locator('.chat-bg-job')).to_be_hidden()
        expect(card.locator('.chat-bg-step')).to_have_count(4)
        expect(card.locator('.chat-bg-job-summary')).to_have_text('2 of 4 steps finished · 1 failed')
        expect(card.locator('.chat-bg-job-health')).to_contain_text('No update for 15m')
        expect(card.locator('.chat-bg-handoff')).to_have_text('CH-000007 resumes when it ends')
        # The resume note is the agent's own instruction for its follow-up turn, not for people.
        expect(card).not_to_contain_text('Read the case logs and report the failures.')
        for state, label in [('done', 'Done'), ('failed', 'Failed'), ('running', 'In progress'), ('pending', 'Pending')]:
            assert card.locator('.chat-bg-step.is-' + state + ' .chat-bg-step-icon').get_attribute('aria-label') == label
        assert card.locator('.is-running .chat-bg-step-icon').evaluate('el => getComputedStyle(el).animationName') == 'none'
        page.emulate_media(reduced_motion='no-preference')
        assert card.locator('.is-running .chat-bg-step-icon').evaluate('el => getComputedStyle(el).animationName') == 'chat-bg-step-spin'
        card.locator('.chat-bg-job-details summary').click()
        expect(card.locator('.chat-bg-output')).to_contain_text('<script>window.injected = true</script>')
        assert not page.evaluate('window.injected || false')
        assert card.locator('a[href="/query_log.html?id=origin7"]').count() == 1
        job['activity']['updated_ts'] = now
        page.evaluate('bg => bw.sync({background: bg}, "7")', bg)
        expect(card.locator('.chat-bg-output')).to_be_visible()  # stays expanded across updates
        expect(card.locator('.chat-bg-job-health')).not_to_have_class(re.compile('is-warning'))
        assert card.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
        # Completion preserves the readable result and delivery receipt, without a running spinner.
        job.update(state='failed', handoff='failed', followup_query_id='follow7')
        page.evaluate('job => bw.sync({background: {watches: [], recent: [job]}}, "7")', job)
        # Nothing live any more: the card shrinks to one line, like a locked form.
        expect(card).to_have_class(re.compile(r'\bis-settled\b'))
        expect(card.locator('.chat-bg-title')).to_have_text('Laptop build and multiplayer checks')
        expect(card.locator('.chat-bg-head .chat-bg-chip')).to_have_text('1 failed')
        expect(card.locator('.chat-bg-rows')).to_have_count(0)
        assert card.evaluate('el => el.getBoundingClientRect().height') < 40
        card.locator('.chat-bg-head').click()
        expect(card.locator('summary.chat-bg-watch .chat-bg-chip')).to_have_text('failed')
        expect(card.locator('.chat-bg-handoff')).to_have_text('Follow-up delivery failed for CH-000007')
        expect(card.locator('.chat-bg-job-health')).to_have_count(0)
        expect(card.locator('.chat-bg-step.is-running')).to_have_count(0)  # ended without a step result
        expect(card.locator('.chat-bg-step.is-unknown')).to_have_count(1)
        assert card.locator('.chat-bg-stop').count() == 0
        assert card.locator('a[href="/query_log.html?id=follow7"]').count() == 1
        page.evaluate('job => bw.sync({background: {watches: [], recent: [{...job, handoff:"delivered"}]}}, "7")', job)
        expect(card.locator('.chat-bg-handoff')).to_have_text('Follow-up delivered to CH-000007')
        assert not errors
    finally:
        context.close()
