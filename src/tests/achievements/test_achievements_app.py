"""Feature App behavior through the browser and authenticated page route."""
import json
from pathlib import Path
import subprocess
import pytest

REPO = Path(__file__).resolve().parents[3]


def test_app_page_authentication(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client
    ctx = _auth_client(tmp_path, monkeypatch)
    assert ctx['client'].get('/achievements_page.html').status_code == 401
    ctx['client'].set_cookie('session_token', ctx['token'])
    response = ctx['client'].get('/achievements_page.html')
    assert response.status_code == 200
    assert b'id="achievementsGrid"' in response.data


def test_layout_upgrade_preserves_existing_pins():
    source = (REPO / 'src/web/js/shell/app_shell.js').read_text()
    start = source.index('function migrateUILayout(saved)')
    end = source.index('// ── Cuttle web apps', start)
    script = '''const assert = require('assert');
const DEFAULT_LAYOUT={}; const DEFAULT_RAIL_HIDDEN=['nav-editor','nav-git','nav-achievements'];
const RAIL_LAYOUT_VERSION=5;
const CANONICAL_RAIL_ITEM_ORDER=['nav-chat','nav-editor','nav-git','nav-achievements','nav-apps'];
''' + source[start:end] + '''
let result=migrateUILayout({layout_version:4,rail_items:['nav-chat','nav-editor','nav-git','nav-apps'],rail_hidden:[]});
assert.deepEqual(result.layout.rail_hidden,['nav-achievements']);
assert(result.layout.rail_items.includes('nav-editor'));
assert(result.layout.rail_items.includes('nav-git'));
result=migrateUILayout({layout_version:5,rail_items:['nav-chat','nav-achievements'],rail_hidden:[]});
assert.equal(result.changed,false);assert(result.layout.rail_items.includes('nav-achievements'));
'''
    subprocess.run(['node', '-e', script], check=True)


def test_app_disabled_enabled_rescan_and_hidden_content(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    from api.achievements import catalog
    rows = catalog.catalog_payload()
    for row in rows:
        row.update(unlocked=False, progress=0, percent=0, seen=False)
    rows[0].update(unlocked=True, percent=100)
    hidden = next(row for row in rows if row.get('hidden'))
    secret_title = hidden['title']
    state = {'enabled': False, 'scans': 0}
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1100, 'height': 800})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route_request(route):
            from urllib.parse import urlparse
            path = urlparse(route.request.url).path
            if path == '/api/achievements':
                payload = ({'success': True, 'items': rows, 'unlocked': 1, 'total': len(rows), 'unseen': 1}
                           if state['enabled'] else {'success': False, 'disabled': True})
                route.fulfill(json=payload)
            elif path == '/api/achievements/scan':
                state['scans'] += 1
                route.fulfill(json={'success': True, 'unlocked': []})
            elif path.startswith('/api/'):
                route.fulfill(json={})
            else:
                file = REPO / 'src/web' / path.lstrip('/')
                if file.is_file():
                    content_type = {'.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html'}.get(file.suffix, 'application/octet-stream')
                    route.fulfill(body=file.read_bytes(), content_type=content_type)
                else:
                    route.fulfill(status=404, body='missing')
        page.route('**/*', route_request)
        page.goto('http://cuttle.test/achievements_page.html')
        page.get_by_text('Achievements is disabled.', exact=False).wait_for()
        assert page.locator('#rescanAchievements').is_disabled()
        assert page.locator('.achievement-card').count() == 0
        state['enabled'] = True
        page.locator('#refreshAchievements').click()
        page.locator('.achievement-card').first.wait_for()
        assert page.locator('.achievement-card').count() == len(rows)
        assert secret_title not in page.locator('#achievementsGrid').inner_text()
        assert page.locator('.is-unlocked').count() == 1
        page.locator('#rescanAchievements').click()
        page.wait_for_function("document.getElementById('achievementsSummary').textContent.includes('unlocked')")
        assert state['scans'] == 1
        page.screenshot(path=str(tmp_path / 'achievements-desktop.png'), full_page=False)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path / 'achievements-mobile.png'), full_page=False)
        state['enabled'] = False
        page.locator('#refreshAchievements').click()
        page.get_by_text('Achievements is disabled.', exact=False).wait_for()
        assert page.locator('.achievement-card').count() == 0
        assert not errors
        browser.close()
