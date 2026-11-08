"""Shared popup palette plus supervision geometry; local static assets, fake host."""
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server, apply_request_guard  # noqa: F401
from .test_panel_themes import THEMES, CONTRAST
from .test_whats_new import _shell_modal_markup


def popup_page(browser, static_server, *, shell=False, width=1024, height=768):
    context = browser.new_context(viewport={'width': width, 'height': height})
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(static_server + '/api/**', lambda route: route.fulfill(json={'success': True}))
    page.goto(static_server + '/agent_feed.html')
    styles = ['ui_boot', 'app_shell' if shell else 'chat_page', 'auth_modal', 'chip_config_modal',
              'searchable_select', 'chat_supervision']
    page.set_content(''.join(f'<link rel="stylesheet" href="{static_server}/css/{s}.css">' for s in styles)
                     + '<button id="opener">Open editor</button><div id="chatSupervisionSlot" hidden></div>')
    for script in ['shared/ui_boot', 'shared/searchable_select', 'chat/chat_chip_config',
                   'chat/chat_supervision_model', 'chat/chat_supervision']:
        page.add_script_tag(url=f'{static_server}/js/{script}.js')
    page.add_style_tag(content='*, *::before, *::after { animation:none !important; transition:none !important; }')
    return context, page


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('shell', [False, True], ids=['chat', 'shell'])
def test_popup_surfaces_and_dropdowns_use_active_palette(browser, static_server, theme, shell):
    context, page = popup_page(browser, static_server, shell=shell)
    try:
        page.evaluate('theme => CuttleUiBoot.applyTheme(theme)', theme)
        page.evaluate('''() => {
            window.chips = CuttleChipConfig.create({});
            chips.openCustom('Agent', 'Choose the agent for this chat',
                '<label class="chip-config-field"><span class="chip-config-field-label">Agent</span>'
                + '<select class="chip-config-select"><option>Codex</option><option>Claude</option></select></label>');
        }''')
        modal = page.locator('.chip-config-modal')
        expected = page.locator('body').evaluate("""el => {const probe=document.createElement('div');
            probe.style='background:var(--popup-bg);color:var(--popup-text)';el.append(probe);
            const s=getComputedStyle(probe), colors=[s.backgroundColor,s.color];probe.remove();return colors;}""")
        for selector in ['.chip-config-modal', '.auth-modal-title', '.auth-modal-subtitle', '.chip-config-field-label', '.cuttle-combo-trigger']:
            colors = page.locator(selector).evaluate('el => [getComputedStyle(el).color, getComputedStyle(document.querySelector(".auth-modal")).backgroundColor]')
            assert page.evaluate(CONTRAST, colors) >= 4.5, (theme, shell, selector, colors)
        assert modal.evaluate('el => getComputedStyle(el).backgroundColor') == expected[0]
        page.locator('.cuttle-combo-trigger').click()
        menu = page.locator('.cuttle-combo-popup')
        expect(menu).to_be_visible()
        assert menu.evaluate('el => getComputedStyle(el).backgroundColor') == expected[0]
        colors = menu.locator('.cuttle-combo-option').last.evaluate('el => [getComputedStyle(el).color,getComputedStyle(el.closest(".cuttle-combo-popup")).backgroundColor]')
        assert page.evaluate(CONTRAST, colors) >= 4.5
        # A theme switch also recolors an already-open floating menu.
        page.evaluate('CuttleUiBoot.applyTheme("matrix")')
        assert menu.evaluate('el => getComputedStyle(el).backgroundColor') == 'rgb(2, 16, 6)'
        page.keyboard.press('Escape')
        page.evaluate('chips.close()')
        # Actual account and release-note markup share the same modal palette.
        html = (Path(__file__).resolve().parents[2] / 'web/app_shell.html').read_text()
        account = re.search(r'<div class="auth-modal-overlay" id="logoutConfirmModal".*?\n</div>', html, re.S).group(0)
        page.locator('body').evaluate('(el, html) => el.insertAdjacentHTML("beforeend", html)', account + _shell_modal_markup())
        page.evaluate('theme => CuttleUiBoot.applyTheme(theme)', theme)
        for id in ['logoutConfirmModal', 'cuttleWhatsNewModal']:
            page.locator('#' + id).evaluate('el => {el.hidden=false;el.classList.add("active")}')
            box = page.locator('#' + id + ' .auth-modal')
            assert box.evaluate('el => getComputedStyle(el).backgroundColor') == expected[0]
            colors = box.locator('.auth-modal-title').evaluate('el => [getComputedStyle(el).color,getComputedStyle(el.closest(".auth-modal")).backgroundColor]')
            assert page.evaluate(CONTRAST, colors) >= 4.5
            page.locator('#' + id).evaluate('el => el.classList.remove("active")')
    finally:
        context.close()


@pytest.mark.parametrize('width,height', [(1024, 768), (768, 1024), (820, 540), (390, 600)])
def test_supervision_editor_fits_and_uses_shared_controls(browser, static_server, width, height):
    context, page = popup_page(browser, static_server, width=width, height=height)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.evaluate('''async () => {
            CuttleUiBoot.applyTheme('mint');
            document.documentElement.style.setProperty('--cuttle-safe-bottom','24px');
            document.documentElement.style.setProperty('--shell-voice-strip-h','40px');
            window.posts=[];
            window.sup=CuttleChatSupervision.create({doc:document,now:()=>Date.now(),
                escapeHtml:s=>String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'),
                enabledFlags:async()=>new Set(['chat_supervision']), setInterval:()=>0,clearInterval:()=>{},toast:()=>{},
                fetch:async(path,opts)=>({ok:true,json:async()=>{
                    if(path==='/api/agents') return {agents:[{id:'codex',label:'Codex',models:['a','b'],efforts:['low','high']}]};
                    if(path.endsWith('/defaults')) return {success:true,handle:'CH-000007',tasks:Array.from({length:16},(_,i)=>({id:'t'+i,title:'Multiplayer checks '+i,done:2,total:5,selected:true}))};
                    if(opts.method==='POST') posts.push(JSON.parse(opts.body));
                    return {success:true,updated:['instructions']};
                }})});
            await new Promise(r=>setTimeout(r,0));
            document.getElementById('opener').focus();
            await sup.openDialog('7',{id:'s7',agent:'codex',every:900,state:'paused',instructions:'Keep working',model_tiers:{worker:['cursor auto']}});
        }''')
        form = page.locator('.chat-supervision-dialog')
        expect(form).to_be_visible()
        assert form.locator('.chat-supervision-dialog-body').evaluate('el => el.scrollTop') == 0
        assert form.get_attribute('aria-modal') == 'true'
        assert form.locator('.cuttle-combo-trigger').count() == 4
        assert form.get_by_role('switch').count() == 16
        footer = form.locator('.chat-supervision-dialog-footer')
        assert form.locator('[type=submit]').evaluate('el => getComputedStyle(el).backgroundColor') == 'rgb(47, 163, 122)'
        bounds = form.bounding_box()
        assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= height - 40 - 24 + 1
        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
        assert form.locator('.chat-supervision-dialog-body').evaluate('el => el.scrollHeight > el.clientHeight')
        assert form.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
        if width == 1024:
            page.screenshot(path='temp/supervision-editor-tablet.png')
        footer_bounds = footer.bounding_box()
        first_switch = form.get_by_role('switch').first
        first_switch.click()
        expect(first_switch).not_to_be_checked()
        form.get_by_role('button', name='Model', exact=True).click()
        menu = page.locator('.cuttle-combo-popup')
        expect(menu).to_be_visible()
        assert form.bounding_box() == bounds
        page.keyboard.press('Escape')
        expect(form).to_be_visible()  # first Escape closes the dropdown only
        form.locator('.chat-supervision-advanced summary').click()
        expect(form.locator('[name=worker]')).to_have_value('cursor auto')
        assert footer.bounding_box() == footer_bounds
        # A reduced viewport (keyboard / Safari chrome) still leaves actions visible.
        page.set_viewport_size({'width': width, 'height': 420})
        assert footer.bounding_box()['y'] + footer.bounding_box()['height'] <= 420 - 40 - 24 + 1
        page.keyboard.press('Escape')
        expect(form).to_have_count(0)
        expect(page.locator('#opener')).to_be_focused()
        assert not page.evaluate('posts')  # opening/closing never resumes or edits anything
        assert not errors
    finally:
        context.close()
