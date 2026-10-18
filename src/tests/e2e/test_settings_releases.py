"""Real browser coverage with all API and external traffic intercepted."""
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[2] / 'web'


def test_release_panel_refresh_notes_and_offline(tmp_path):
    payload = {'current_version': '0.2.26', 'git_rev': 'abc123', 'state': 'available',
               'checked_at': 1791153614, 'release': {
                   'version': '0.3.0', 'title': 'New features',
                   'published_at': '2026-10-04T00:00:00Z',
                   'url': 'https://github.com/kidchemical/Cuttle/releases/tag/v0.3.0',
                   'notes': '# What changed\n<script>window.pwned=true</script>\n- Improved updates'}}
    checks = []
    offline = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 420, 'height': 900})
        def route(request):
            url = request.request.url
            path = urlparse(url).path
            if path == '/api/settings/releases':
                checks.append(url)
                if offline:
                    request.abort()
                else:
                    request.fulfill(content_type='application/json', body=json.dumps(payload))
            elif path.startswith('/api/'):
                request.fulfill(content_type='application/json', body='{}')
            elif url.startswith('http://cuttle.test/'):
                file = WEB / path.lstrip('/')
                if file.is_file():
                    request.fulfill(path=str(file))
                else:
                    request.fulfill(status=404, body='')
            else:
                request.abort()
        page.route('**/*', route)
        page.goto('http://cuttle.test/settings_page.html?tab=general')
        status = page.locator('[data-release-status]')
        page.wait_for_function("document.querySelector('[data-release-status]').textContent.includes('0.3.0')")
        page.locator('#cuttleReleaseSettings summary').click()
        assert page.locator('[data-release-notes]').inner_text().startswith('# What changed')
        assert page.evaluate('window.pwned') is None
        assert page.locator('[data-release-link]').get_attribute('href').endswith('/tag/v0.3.0')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(tmp_path / 'release-settings.png'), full_page=True)
        page.locator('[data-release-check]').click()
        page.wait_for_function("!document.querySelector('[data-release-check]').disabled")
        assert checks[-1].endswith('?force=1') and len(checks) == 2
        offline = True
        page.locator('[data-release-check]').click()
        page.wait_for_function("document.querySelector('[data-release-status]').textContent.includes('unavailable')")
        assert page.locator('[data-release-check]').is_enabled()
        assert page.locator('[data-release-notes]').is_visible()
        assert 'unavailable' in status.inner_text()
        # A stale cached success explicitly warns that these are saved notes.
        offline = False
        payload.update(error='Could not check GitHub releases.', stale=True)
        page.locator('[data-release-check]').click()
        page.wait_for_function("document.querySelector('[data-release-status]').textContent.includes('saved release notes')")
        assert 'saved result' in page.locator('[data-release-checked]').inner_text()

        # Automatic checks are device-local, on by default, and manual checks
        # still work after opting out.
        page.locator('[data-release-auto-toggle]').click()
        assert page.locator('[data-release-auto-check]').is_checked() is False
        page.reload()
        page.wait_for_function("document.querySelector('[data-release-status]').textContent.includes('Automatic checks are off')")
        assert len(checks) == 4
        page.locator('[data-release-check]').click()
        page.wait_for_function("!document.querySelector('[data-release-check]').disabled")
        assert len(checks) == 5 and checks[-1].endswith('?force=1')
        browser.close()


def _developer_page(p, snapshot, posts):
    page = p.chromium.launch(headless=True).new_page(viewport={'width': 900, 'height': 1000})

    def route(request):
        path = urlparse(request.request.url).path
        if path == '/api/settings/updates':
            request.fulfill(content_type='application/json', body=json.dumps(snapshot))
        elif path == '/api/settings/developer-mode':
            body = json.loads(request.request.post_data or '{}')
            posts.append(body)
            snapshot['developer_mode'] = {'enabled': body['enabled'], 'available': True, 'explicit': True}
            request.fulfill(content_type='application/json', body=json.dumps(
                {'success': True, 'developer_mode': snapshot['developer_mode']}))
        elif path.startswith('/api/'):
            request.fulfill(content_type='application/json', body='{}')
        elif request.request.url.startswith('http://cuttle.test/'):
            file = WEB / path.lstrip('/')
            request.fulfill(path=str(file)) if file.is_file() else request.fulfill(status=404, body='')
        else:
            request.abort()
    page.route('**/*', route)
    page.goto('http://cuttle.test/settings_page.html?tab=general')
    return page


def test_developer_rows_only_on_source_checkouts():
    """Release installs see Check for updates only; Developer mode gates the rest."""
    with sync_playwright() as p:
        page = _developer_page(p, {'install_kind': 'release',
                                   'developer_mode': {'enabled': False, 'available': False}}, [])
        page.wait_for_function("document.querySelector('[data-release-status]').textContent !== 'Loading release information…'")
        page.wait_for_timeout(300)
        assert page.locator('[data-release-check]').is_visible()
        assert page.locator('[data-developer-mode-row]').is_hidden()
        assert page.locator('[data-developer-row]').first.is_hidden()
        assert page.locator('[data-device-update]').count() == 0  # the old one-click updater is gone
        page.context.browser.close()

        posts = []
        page = _developer_page(p, {'install_kind': 'source',
                                   'developer_mode': {'enabled': True, 'available': True}}, posts)
        page.wait_for_selector('[data-developer-mode-row]:not([hidden])')
        assert page.locator('[data-developer-row]').first.is_visible()
        page.locator('[data-developer-mode-toggle]').click()
        page.wait_for_function("document.querySelector('[data-developer-row]').hidden")
        assert posts == [{'enabled': False}]
        assert page.locator('[data-developer-mode-row]').is_visible()
        page.context.browser.close()
