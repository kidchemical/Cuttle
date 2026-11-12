"""Background-work card on a phone: a lone supervision stays one line."""
import json
import os
import time

import pytest
from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401
from .test_chat_background_work import SCHEDULED

MONO = "ui-monospace, 'Cascadia Code', 'JetBrains Mono', Menlo, Consolas, monospace"


def _supervision(feed, paused):
    live = json.loads(json.dumps(SCHEDULED))
    bg = live['background']
    bg['feed'] = feed
    if paused:
        bg['schedules'][0].update(status='paused', target='CH-002353')
    else:
        bg['schedules'][0].update(target='CH-002353', kind='supervise', every=900, seen='waiting_background',
                                  next_at=time.time() + 450)
    return live


@pytest.mark.parametrize('font', ['', MONO])
@pytest.mark.parametrize('feed', [True, False])
@pytest.mark.parametrize('paused', [True, False])
def test_lone_supervision_is_one_line_on_a_phone(browser, static_server, font, feed, paused):
    page = browser.new_page(viewport={'width': 390, 'height': 700})
    try:
        page.goto(static_server + '/agent_feed.html')
        style = '<style>body{font-family:%s}</style>' % font if font else ''
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">' + style
                         + '<div id="chatMessages" class="chat-messages"></div>')
        page.add_script_tag(url=static_server + '/js/shared/progress_model.js')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate('''() => { window.bw = CuttleChatBackgroundWork.create({win: window,
            currentSession: () => '1822', sessionIdsEqual: (a, b) => a === b, isBusy: () => false}); }''')
        page.evaluate('live => bw.sync(live, "1822")', _supervision(feed, paused))
        card = page.locator('#chat-background-work')
        expect(card.locator('.chat-bg-title')).to_have_text('Supervision paused' if paused else 'Supervising')
        shots = os.environ.get('BG_PHONE_SHOTS')
        if shots:
            card.screenshot(path='%s/bg_%s_%s_%s.png' % (shots, 'paused' if paused else 'live',
                                                         'mono' if font else 'sans', 'feed' if feed else 'plain'))
        m = card.evaluate('''el => {
            const head = el.querySelector('.chat-bg-head');
            const t = el.querySelector('.chat-bg-title');
            const kids = [...head.children].filter(k => k.getBoundingClientRect().width > 0);
            const tops = kids.map(k => Math.round(k.getBoundingClientRect().top + k.getBoundingClientRect().height / 2));
            return {card: el.getBoundingClientRect().height, head: head.getBoundingClientRect().height,
                    rows: Math.max(...tops) - Math.min(...tops), clipped: t.scrollWidth > t.clientWidth + 1,
                    right: head.getBoundingClientRect().right,
                    lastRight: kids[kids.length - 1].getBoundingClientRect().right};
        }''')
        assert m['rows'] <= 2, m          # every header item on the same line
        assert not m['clipped'], m        # "Supervision paused" reads in full
        assert m['card'] < 48, m          # one line, not a tall empty box
        if feed:
            expect(card.locator('.chat-bg-details')).to_be_visible()
            assert m['right'] - m['lastRight'] < 16, m  # Feed icon sits at the right end
    finally:
        page.close()
