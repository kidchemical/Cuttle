"""Shared Cuttle error page in Chromium: server-rendered, Electron network, Android bridge."""
import json
from urllib.parse import urlencode

import pytest
from .test_shared_diff_modal import static_server, browser, apply_request_guard
from api import error_pages


@pytest.fixture
def page(browser, static_server):
    context = browser.new_context(viewport={'width': 900, 'height': 820})
    apply_request_guard(context, static_server)
    pg = context.new_page()
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    yield pg
    assert errors == []
    context.close()


def test_server_rendered_404(page, static_server):
    data = {'kind': 'http', 'status': 404, 'name': 'Not Found', 'path': '/old/link', 'method': 'GET',
            'request_id': 'abc123', 'time': '2026-11-17T09:00:00Z', 'platform': 'server'}
    html = error_pages.PAGE.read_text(encoding='utf-8').replace(error_pages.MARKER, json.dumps(data))
    page.route('**/old/link', lambda r: r.fulfill(status=404, content_type='text/html', body=html))
    page.goto(static_server + '/old/link')
    assert page.locator('#errCode').inner_text() == '404'
    assert page.locator('#errTitle').inner_text() == 'Not found'
    assert page.locator('#errLead code').inner_text() == '/old/link'
    assert 'Retry' not in page.locator('#errActions button').all_inner_texts()  # 404 is not retryable
    assert 'Open chat' in page.locator('#errActions button').all_inner_texts()
    page.locator('#errDetails summary').click()
    assert 'abc123' in page.locator('#errList').inner_text()
    assert page.locator('#errRetry').inner_text() == ''  # no auto-retry for 404
    page.screenshot(path='temp/err_shot/404.png')


def test_electron_connection_refused_counts_down_and_retries(page, static_server):
    target = static_server + '/landed.html'
    page.route('**/landed.html', lambda r: r.fulfill(status=200, content_type='text/html', body='<p id=ok>back</p>'))
    q = urlencode({'kind': 'network', 'platform': 'electron', 'code': 'ERR_CONNECTION_REFUSED', 'errno': -102,
                   'url': target, 'attempt': 0})
    page.clock.install()
    page.goto(static_server + '/error_page.html?' + q)
    assert page.locator('#errCode').inner_text() == 'ERR_CONNECTION_REFUSED'
    assert page.locator('#errTitle').inner_text() == 'Cuttle isn’t answering'
    assert page.locator('#errRetry').inner_text() == 'Retrying in 5s'
    assert 'tray icon' in page.locator('#errFoot').inner_text()
    page.screenshot(path='temp/err_shot/electron.png')
    page.clock.run_for(5500)
    page.locator('#ok').wait_for()


def test_android_uses_native_bridge_and_message(page, static_server):
    page.add_init_script("window.__calls=[]; window.CuttleShellNative={reload:()=>__calls.push('reload'), openSettings:()=>__calls.push('settings')}")
    q = urlencode({'kind': 'network', 'platform': 'android', 'code': 'ERR_CERT_AUTHORITY_INVALID',
                   'url': 'https://192.168.1.5:8080', 'message': "This PC's certificate changed."})
    page.goto(static_server + '/error_page.html?' + q)
    assert page.locator('#errTitle').inner_text() == 'Certificate not trusted'
    assert page.locator('#errLead').inner_text() == "This PC's certificate changed."
    assert page.locator('#errRetry').inner_text() == ''  # cert errors don't auto-retry
    page.get_by_role('button', name='Server settings').click()
    page.get_by_role('button', name='Retry').click()
    assert page.evaluate('window.__calls') == ['settings', 'reload']
