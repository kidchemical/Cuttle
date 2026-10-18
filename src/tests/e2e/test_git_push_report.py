"""Isolated popup/browser checks; no live push or hosting-runtime changes."""
import json
from tests.e2e.test_shared_diff_modal import browser, static_server, surface  # noqa: F401

REPORT = {
    'version': 1,
    'summary': 'Pre-push secret hook blocked the push: secret-patterns, sqlite-secrets',
    'findings': [
        {'hook': 'secret-patterns', 'rule': 'private key', 'file': 'src/tests/git/test_github_app.py', 'commit': 'dc85768c', 'line': 372, 'preview': '[Flagged line redacted]'},
        {'hook': 'sqlite-secrets', 'rule': 'AWS access key', 'file': 'fixture.db', 'commit': 'abcdef', 'table': 'config', 'column': 'value', 'row': 2},
    ],
    'warnings': [],
}


def test_chat_action_report_opens_and_escapes_untrusted_filename(surface):
    page, api, errors = surface('chat_page.html')
    report = dict(REPORT, findings=[dict(REPORT['findings'][0], file='<img src=x onerror=alert(1)>')])
    response = {'results': [{'action': 'git.push', 'response': 'CUTTLE_PUSH_REPORT=' + json.dumps(report)}]}
    summary = page.evaluate('(data) => CuttleGitPushReport.fromAction(data)', response)
    assert 'secret-patterns' in summary
    dialog = page.locator('#cuttle-push-report')
    assert dialog.is_visible()
    assert dialog.locator('img').count() == 0
    assert '<img src=x' in dialog.inner_text()
    dialog.get_by_role('button', name='Close').click()
    assert not errors


def test_review_exception_saves_only_after_reason_and_handles_server_error(surface):
    page, api, errors = surface('chat_page.html')
    requests = []
    def save(route):
        requests.append(route.request.post_data_json)
        data = {'success': True} if len(requests) > 1 else {'success': False, 'error': 'Policy is read-only'}
        route.fulfill(status=200 if data['success'] else 400, content_type='application/json', body=json.dumps(data))
    page.route('**/api/git/push-exceptions', save)
    page.evaluate('(r) => CuttleGitPushReport.show(r, {projectPath:"/repo/Cuttle",repoRoot:"/repo/Cuttle/nested-repo"})', REPORT)
    dialog = page.locator('#cuttle-push-report')
    assert dialog.get_by_role('button', name='Allow this finding').count() == 1
    dialog.get_by_role('button', name='Allow this finding').click()
    reason = dialog.get_by_label('Why is this safe to allow?')
    reason.fill('Reviewed test fixture')
    assert not requests
    dialog.get_by_role('button', name='Save exception').click()
    page.get_by_text('Policy is read-only', exact=True).wait_for()
    assert reason.input_value() == 'Reviewed test fixture'
    dialog.get_by_role('button', name='Save exception').click()
    dialog.get_by_role('button', name='Allowed', exact=True).wait_for()
    assert dialog.get_by_role('button', name='Allowed', exact=True).is_disabled()
    assert len(requests) == 2
    assert requests[1] == dict(path='/repo/Cuttle', repo_root='/repo/Cuttle/nested-repo', finding=REPORT['findings'][0], reason='Reviewed test fixture')
    assert not errors


def test_report_is_centered_blurred_scrollable_and_mobile_safe(surface, tmp_path):
    page, api, errors = surface('chat_page.html')
    page.evaluate('(r) => CuttleGitPushReport.show(r, {projectPath:"/repo/Cuttle"})', REPORT)
    page.wait_for_function("getComputedStyle(document.querySelector('#cuttle-push-report')).display === 'flex'")
    card = page.locator('.push-report-card').bounding_box()
    assert abs(card['x'] + card['width'] / 2 - 640) < 2
    assert abs(card['y'] + card['height'] / 2 - 400) < 2
    blur = page.locator('#cuttle-push-report').evaluate("el => getComputedStyle(el, '::backdrop').backdropFilter")
    assert blur == 'blur(5px)'
    page.screenshot(path=str(tmp_path / 'push-report-desktop.png'))
    page.set_viewport_size({'width': 390, 'height': 680})
    card = page.locator('.push-report-card').bounding_box()
    assert card['x'] >= 10 and card['x'] + card['width'] <= 380
    assert card['y'] >= 10 and card['y'] + card['height'] <= 670
    assert page.locator('.push-report-body').evaluate('el => el.scrollHeight > el.clientHeight')
    page.screenshot(path=str(tmp_path / 'push-report-mobile.png'))
    page.keyboard.press('Escape')
    page.locator('#cuttle-push-report').wait_for(state='detached')
    assert not errors


def test_report_from_pane_uses_app_window_and_diff_stays_above_report(surface):
    page, api, errors = surface('chat_page.html')
    page.evaluate('''() => {
        const pane = document.createElement('iframe');
        pane.id = 'testChatPane'; pane.src = '/chat_page.html';
        pane.style.cssText = 'position:fixed;width:350px;height:300px;right:0;bottom:0';
        document.body.appendChild(pane);
    }''')
    frame = page.locator('#testChatPane').element_handle().content_frame()
    frame.wait_for_function('typeof CuttleGitPushReport !== "undefined"')
    frame.evaluate('(r) => CuttleGitPushReport.show(r, {projectPath:"/repo/Cuttle",repoRoot:"/repo/Cuttle/nested-repo"})', REPORT)
    assert frame.locator('#cuttle-push-report').count() == 0
    dialog = page.locator('#cuttle-push-report')
    page.wait_for_function("getComputedStyle(document.querySelector('#cuttle-push-report')).display === 'flex'")
    assert dialog.bounding_box()['width'] == 1280
    # Also exercise loading the shared diff viewer into a host without it.
    page.evaluate('delete window.CuttleDiffModal')
    dialog.get_by_role('button', name='View flagged commit diff').first.click()
    diff = page.locator('#cuttleDiffModal')
    diff.wait_for(state='visible')
    diff.get_by_role('button', name='Close diff', exact=True).click()
    assert dialog.is_visible()
    dialog.get_by_role('button', name='Close', exact=True).click()
    assert not errors


def test_report_matches_account_panel_in_actual_shell(browser, static_server):
    from tests.e2e.test_app_shell_layout import ShellWorld, _open, _load_shell
    context, page, errors, blocked = _open(browser, static_server, ShellWorld())
    try:
        _load_shell(page, static_server)
        page.add_script_tag(url=static_server + '/js/git/git_push_report.js')
        page.evaluate('(r) => CuttleGitPushReport.show(r, {projectPath:"/repo/Cuttle"})', REPORT)
        page.wait_for_function("getComputedStyle(document.querySelector('#cuttle-push-report')).display === 'flex'")
        styles = page.evaluate('''() => {
            const report = document.querySelector('.push-report-card');
            const account = document.querySelector('#logoutConfirmModal .auth-modal');
            return {
                report: getComputedStyle(report).backgroundImage,
                account: getComputedStyle(account).backgroundImage,
                reportRadius: getComputedStyle(report).borderRadius,
                accountRadius: getComputedStyle(account).borderRadius,
                reportBlur: getComputedStyle(document.querySelector('#cuttle-push-report'), '::backdrop').backdropFilter,
                accountBlur: getComputedStyle(document.querySelector('#logoutConfirmModal')).backdropFilter,
            };
        }''')
        assert styles['report'] == styles['account']
        assert styles['reportRadius'] == styles['accountRadius']
        assert styles['reportBlur'] == styles['accountBlur']
        page.get_by_role('button', name='Dismiss push review', exact=True).click()
        assert not errors
    finally:
        context.close()
