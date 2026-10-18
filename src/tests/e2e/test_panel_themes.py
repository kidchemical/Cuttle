"""Glass surfaces and real gizmo previews across the shipped theme palettes."""
import re
import time
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from .test_shared_diff_modal import browser, static_server, apply_request_guard  # noqa: F401

THEMES = ['dark', 'midnight', 'light', 'sakura', 'mint', 'lavender', 'paper',
          'graphite', 'nord', 'rgb', 'synthwave', 'glitch', 'matrix']

# Resolve CSS Color 4 values through canvas, compositing translucent colors.
CONTRAST = '''([foreground, background]) => {
    const ctx = document.createElement('canvas').getContext('2d');
    function lum(color) {
        ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 1, 1);
        ctx.fillStyle = background; ctx.fillRect(0, 0, 1, 1);
        ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1);
        const c = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => {
            v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
        });
        return .2126*c[0] + .7152*c[1] + .0722*c[2];
    }
    const a = lum(foreground), b = lum(background);
    return (Math.max(a,b)+.05) / (Math.min(a,b)+.05);
}'''


@pytest.mark.parametrize('theme', THEMES)
def test_gizmo_previews_docks_and_unselected_controls_follow_theme(browser, static_server, theme):
    context = browser.new_context(viewport={'width': 1200, 'height': 900})
    apply_request_guard(context, static_server)
    page = context.new_page()
    gizmo = {'id': 'theme-meter', 'type': 'usage_meter', 'title': 'Codex usage',
             'config': {'agent': 'codex', 'window': 'tightest', 'show': 'remaining'},
             'placement': {'dock': 'float', 'order': 0}}
    data = {'label': 'Codex', 'updated_at': time.time(), 'windows': [
        {'id': 'five_hour', 'label': '5-hour', 'used_percent': 40, 'remaining_percent': 60,
         'reset_at': time.time() + 3600}]}

    def handle(route):
        path = urlparse(route.request.url).path
        payload = {'success': True}
        if path == '/api/gizmos':
            payload.update(enabled=True, revision=1, gizmos=[gizmo], types=[{
                'id': 'usage_meter', 'label': 'Usage meter', 'options': {
                    'agents': [{'agent': 'codex', 'label': 'Codex'}], 'metrics': ['auto']}}])
        elif path.endswith('/data'):
            payload['data'] = data
        route.fulfill(json=payload)

    page.route(static_server + '/api/**', handle)
    try:
        page.goto(static_server + '/gizmos_page.html', wait_until='networkidle')
        expect(page.locator('.gizmo--card .gizmo-value').first).to_contain_text('60')
        page.add_style_tag(content='*, *::before, *::after { transition: none !important; animation: none !important; }')
        page.evaluate('(theme) => CuttleUiBoot.applyTheme(theme)', theme)
        # Same renderer used in shell docks; include an unselected popover control.
        page.evaluate('''({gizmo, data}) => {
            const probe = document.createElement('section');
            probe.id = 'dockProbes'; probe.style = 'display:flex;gap:12px;margin-top:20px';
            probe.innerHTML = ['titlebar','rail','float'].map(v => CuttleGizmos.renderGizmoHtml(gizmo,data,v,Date.now())).join('');
            document.querySelector('main').append(probe);
            const popover = document.createElement('div');
            popover.className = 'shell-gizmo-popover'; popover.style = 'position:relative;margin-top:20px';
            popover.innerHTML = CuttleGizmos.renderDetailHtml(gizmo, data, Date.now()) + '<div class="shell-gizmo-popover-docks"><button>Title bar</button><button class="is-current">Float</button></div>';
            document.querySelector('main').append(popover);
        }''', {'gizmo': gizmo, 'data': data})
        for selector in ['.gizmos-create', '.gizmo-item']:
            assert page.locator(selector).evaluate('''el => {
                const s = getComputedStyle(el), ctx = document.createElement('canvas').getContext('2d');
                ctx.fillStyle = s.backgroundColor; ctx.fillRect(0,0,1,1);
                const alpha = ctx.getImageData(0,0,1,1).data[3];
                return s.backdropFilter.includes('blur') && alpha > 150 && alpha < 255;
            }''')
        for selector in ['.gizmo--card', '#dockProbes .gizmo--float']:
            colors = page.locator(selector).evaluate('el => {const s=getComputedStyle(el);return [s.color,s.backgroundColor]}')
            assert page.evaluate(CONTRAST, colors) >= 4.5, (theme, selector, colors)
        # Detail rows render outside `.gizmo`; they must still get theme text.
        colors = page.evaluate('''() => {
            const host = document.querySelector('.shell-gizmo-popover');
            return [getComputedStyle(host.querySelector('.gizmo-row-label')).color, getComputedStyle(host).backgroundColor];
        }''')
        assert page.evaluate(CONTRAST, colors) >= 4.5, (theme, 'popover row label', colors)
        for selector in ['.gizmo-item-actions button:not(.gizmos-danger)', '.shell-gizmo-popover button:not(.is-current)']:
            # Translucent inks are judged against the opaque surface they sit on.
            colors = page.locator(selector).evaluate('''el => {
                const s = getComputedStyle(el), host = el.closest('.shell-gizmo-popover');
                return [s.borderTopColor, host ? getComputedStyle(host).backgroundColor : s.backgroundColor];
            }''')
            assert page.evaluate(CONTRAST, colors) >= 1.5, (theme, selector, colors)
        assert page.locator('.shell-gizmo-popover button.is-current').evaluate('el => getComputedStyle(el).borderColor') != page.locator('.shell-gizmo-popover button:not(.is-current)').evaluate('el => getComputedStyle(el).borderColor')
        if theme in ['dark', 'light', 'sakura', 'matrix']:
            page.screenshot(path=f'temp/ui-gizmos-theme-{theme}.png')
        page.set_viewport_size({'width': 320, 'height': 900})
        page.locator('#dockProbes').evaluate('el => el.remove()')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        context.close()


@pytest.mark.parametrize('theme', ['light', 'dark', 'sakura', 'matrix'])
def test_popout_loads_saved_theme_before_rendering(browser, static_server, theme):
    context = browser.new_context(viewport={'width': 320, 'height': 160})
    apply_request_guard(context, static_server)
    context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
    page = context.new_page()
    gizmo = {'id': 'popout', 'type': 'usage_meter', 'title': 'Usage',
             'config': {'agent': 'codex'}, 'placement': {'dock': 'popout'}}
    page.route(static_server + '/api/**', lambda route: route.fulfill(json={
        'success': True, 'gizmo': gizmo, 'data': {'label': 'Codex', 'updated_at': time.time(),
        'windows': [{'id': 'hour', 'label': 'Hourly', 'remaining_percent': 60, 'used_percent': 40}]}}))
    try:
        page.goto(static_server + '/gizmo_popout.html?id=popout', wait_until='networkidle')
        expect(page.locator('.gizmo--popout')).to_be_visible()
        if theme not in ['light', 'dark']:
            expect(page.locator('html')).to_have_class(re.compile('theme-' + theme))
        colors = page.locator('body').evaluate('el => {const s=getComputedStyle(el);return [s.color,s.backgroundColor]}')
        assert page.evaluate(CONTRAST, colors) >= 4.5
        assert page.locator('.gizmo--popout').evaluate('el => getComputedStyle(el).color') == colors[0]
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        context.close()
