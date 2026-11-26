"""Media App in Chromium: grid, filters, sort, chat links and the chat lightbox slideshow."""
import base64
import json
from urllib.parse import urlparse

import pytest
from .control_helpers import choose
from .test_shared_diff_modal import static_server, browser, apply_request_guard

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def _item(i, chat, name, when, kind='image', source='agent', available=True, size=100, title='', project='Galaxy'):
    url = f'/output/shared/{name}'
    return {'id': f'{i}:0', 'url': url, 'kind': kind, 'name': name, 'title': title, 'source': source,
            'role': 'assistant', 'message_id': i, 'share_index': i, 'handle': f'CH-{chat:06d}-{i}',
            'shared_at': when, 'chat_id': chat, 'chat_title': {11: 'Ship art', 12: 'Bug hunt'}[chat],
            'project': project, 'chat_archived': False, 'chat_starred': False,
            'available': available, 'size': size if available else None, 'refs': 1}


ITEMS = [
    _item(5, 11, 'zeta.png', '2026-11-17T10:00:00Z', title='Final icon', size=900),
    _item(4, 12, 'clip.mp4', '2026-11-17T09:00:00Z', kind='video', size=5000),
    _item(3, 11, 'alpha.png', '2026-11-16T09:00:00Z', source='upload', size=50),
    _item(2, 12, 'gone.png', '2026-11-10T09:00:00Z', available=False),
    _item(1, 12, 'beta.png', '2026-11-01T09:00:00Z', size=300, project=''),
]
CHATS = [{'id': 11, 'handle': 'CH-000011', 'title': 'Ship art', 'project': 'Galaxy', 'archived': False, 'count': 2, 'last': ITEMS[0]['shared_at']},
         {'id': 12, 'handle': 'CH-000012', 'title': 'Bug hunt', 'project': 'Galaxy', 'archived': False, 'count': 3, 'last': ITEMS[1]['shared_at']}]


def _handle(route):
    path = urlparse(route.request.url).path
    if path == '/api/media-library':
        body = {'success': True, 'items': ITEMS, 'chats': CHATS,
                'totals': {'items': 5, 'images': 4, 'videos': 1, 'available': 4}}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))
    elif path.startswith('/api/'):
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'success': True}))
    elif path.startswith('/output/'):
        route.fulfill(status=200, content_type='image/png', body=PNG)


@pytest.fixture
def page(browser, static_server):
    context = browser.new_context(viewport={'width': 1240, 'height': 900})
    apply_request_guard(context, static_server)
    pg = context.new_page()
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.route('**/api/**', _handle)
    pg.route('**/output/**', _handle)
    pg.add_init_script("if (!sessionStorage.mediaTest) { sessionStorage.mediaTest = 1; localStorage.removeItem('cuttle.mediaLibrary.prefs.v1'); }")
    pg.goto(static_server + '/media_page.html')
    pg.locator('.media-tile').first.wait_for()
    yield pg
    assert errors == []
    context.close()


def names(pg):
    return pg.locator('.media-tile .media-name').all_inner_texts()


def test_newest_first_without_expired_then_sort_filter_and_expired(page):
    assert names(page) == ['zeta.png', 'clip.mp4', 'alpha.png', 'beta.png']
    assert page.locator('#mediaSummary').inner_text() == '4 of 5'
    assert page.locator('.media-tile').first.locator('.media-handle').inner_text() == 'CH-000011-5'

    choose(page, page.locator('#mediaSort'), 'size-desc')
    assert names(page) == ['clip.mp4', 'zeta.png', 'beta.png', 'alpha.png']
    page.locator('.media-seg[data-pref="kind"] button[data-value="image"]').click()
    assert names(page) == ['zeta.png', 'beta.png', 'alpha.png']
    choose(page, page.locator('#mediaSource'), 'mine')
    assert names(page) == ['alpha.png']
    page.locator('#mediaClear').click()
    assert names(page) == ['clip.mp4', 'zeta.png', 'beta.png', 'alpha.png']  # sort kept

    page.check('#mediaShowExpired')
    assert 'gone.png' in names(page)
    assert page.locator('.media-tile.is-expired .media-expired').count() == 1
    page.fill('#mediaSearch', 'bug hunt')
    page.wait_for_function("document.querySelectorAll('.media-tile').length === 3")
    # Prefs survive a reload.
    page.reload()
    page.locator('.media-tile').first.wait_for()
    assert page.locator('#mediaSort').input_value() == 'size-desc'
    assert page.locator('#mediaShowExpired').is_checked()


def test_click_opens_lightbox_over_the_shown_list(page):
    page.locator('.media-tile[data-id="3:0"] .cuttle-media-thumb').click()
    box = page.locator('.cuttle-lightbox')
    box.wait_for()
    # Gallery spans every available shown item (4), starting at the clicked one.
    assert box.locator('.cuttle-lightbox-counter').inner_text() == '3 / 4'
    page.keyboard.press('ArrowRight')
    assert box.locator('.cuttle-lightbox-counter').inner_text() == '4 / 4'
    assert box.locator('.cuttle-lightbox-stage img').get_attribute('src') == '/output/shared/beta.png'
    page.keyboard.press('ArrowRight')  # wraps to the first
    assert box.locator('.cuttle-lightbox-stage img').get_attribute('src') == '/output/shared/zeta.png'
    assert 'Ship art' in box.locator('.cuttle-lightbox-desc').inner_text()
    page.keyboard.press('Escape')
    assert box.is_hidden()


def test_group_by_chat_and_chat_links(page):
    choose(page, page.locator('#mediaGroup'), 'chat')
    heads = page.locator('.media-group-head > span:first-child').all_inner_texts()
    assert heads == ['Ship art', 'Bug hunt']  # each chat once, newest share first
    page.locator('.media-tile[data-id="1:0"] [data-filter-chat]').click()
    assert names(page) == ['clip.mp4', 'beta.png']
    assert 'chat=12' in page.url
    link = page.locator('.media-tile[data-id="1:0"] .media-chat-link')
    assert link.get_attribute('href') == '/chat_page.html?chat=12&msg=1'


def test_paging_appends_fills_tall_screens_and_keeps_groups_whole(browser, static_server):
    many = [_item(1000 - i, 11 if i % 2 else 12, f'img{i:03d}.png',
                  f'2026-11-{17 - i // 30:02d}T{23 - i % 24:02d}:00:00Z') for i in range(300)]
    for i, it in enumerate(many):
        it['id'] = f'm{i}'

    def handle(route):
        path = urlparse(route.request.url).path
        if path == '/api/media-library':
            route.fulfill(status=200, content_type='application/json', body=json.dumps(
                {'success': True, 'items': many, 'chats': CHATS, 'totals': {}}))
        elif path.startswith('/output/'):
            route.fulfill(status=200, content_type='image/png', body=PNG)
        else:
            route.fulfill(status=200, content_type='application/json', body='{"success": true}')

    context = browser.new_context(viewport={'width': 1600, 'height': 4600})
    apply_request_guard(context, static_server)
    pg = context.new_page()
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.route('**/api/**', handle)
    pg.route('**/output/**', handle)
    pg.add_init_script("""if (!sessionStorage.pagingTest) { sessionStorage.pagingTest = 1;
        localStorage.setItem('cuttle.mediaLibrary.prefs.v1', JSON.stringify({size: 'small'})); }""")
    pg.goto(static_server + '/media_page.html')
    pg.locator('.media-tile').first.wait_for()
    # 120 small tiles (~3700px) do not reach the bottom of a 4600px window: it keeps loading by itself.
    pg.wait_for_function("document.querySelectorAll('.media-tile').length > 120")
    first = pg.locator('.media-tile').first
    first.evaluate("el => { el.__kept = true; }")
    before = pg.locator('.media-tile').count()
    pg.mouse.wheel(0, 100000)
    pg.wait_for_function(f"document.querySelectorAll('.media-tile').length > {before}")
    assert first.evaluate("el => el.__kept === true")  # appended, not redrawn

    # Group by chat: two sections only, headed with full totals, still whole after paging.
    choose(pg, pg.locator('#mediaGroup'), 'chat')
    pg.wait_for_function("document.querySelectorAll('.media-group').length === 2")
    assert pg.locator('.media-group-count').all_inner_texts() == ['150', '150']
    pg.mouse.wheel(0, 100000)
    pg.wait_for_function("document.querySelectorAll('.media-tile').length === 300")
    assert pg.locator('.media-group').count() == 2
    assert pg.locator('#mediaMore').is_hidden()
    assert errors == []
    context.close()
