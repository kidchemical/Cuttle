"""Shared contextual-help affordance, using an isolated local browser page."""
from pathlib import Path
import pytest

WEB = Path(__file__).parents[2] / "web"


@pytest.mark.parametrize("touch", [False, True])
def test_shared_info_hover_focus_tap_and_legacy_meter(touch):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except playwright.Error:
            pytest.skip("Playwright Chromium unavailable")
        try:
            context = browser.new_context(has_touch=touch, is_mobile=touch,
                timezone_id="Asia/Tokyo" if touch else "America/Los_Angeles",
                viewport={"width": 390 if touch else 800, "height": 600})
            page = context.new_page()
            page.set_default_timeout(5000)
            page.set_content('<body><main></main><button id="ordinary" data-tooltip="Ordinary help">Settings</button></body>')
            page.add_style_tag(path=str(WEB / "css/ui_boot.css"))
            page.add_style_tag(path=str(WEB / "css/chat_page.css"))
            page.add_script_tag(path=str(WEB / "js/shared/ui_boot.js"))
            page.add_script_tag(path=str(WEB / "js/chat/chat_messages.js"))
            page.evaluate('''() => {
                const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
                const rows = [
                    {label:'5-hour',pct:80,tooltip:'Resets',tooltip_at:1790926320},
                    {label:'Weekly (resets Oct 02, 2026 07:32 UTC)',pct:97},
                    {label:'Credits',pct:0,disabled:true,status:'None'}
                ];
                const extracted = CuttleChatMessages.extractTailStructuredBlocks(
                    '<cuttle_meters>'+JSON.stringify({rows})+'</cuttle_meters>',
                    {escapeHtmlInline:esc,clamp:(n,a,b)=>Math.min(b,Math.max(a,n))});
                document.querySelector('main').innerHTML = CuttleChatMessages.restoreStructuredBlocks(extracted.text,extracted.blocks);
            }''')
            assert page.locator('.cuttle-info').count() == 2
            assert page.locator('.cuttle-meter-label').all_text_contents() == ['5-houri', 'Weeklyi', 'Credits']
            icon = page.locator('.cuttle-info').first
            tooltip = page.locator('#cuttle-shared-tooltip')
            if touch:
                page.locator('#ordinary').tap()
                assert page.locator('.cuttle-tooltip.is-visible').count() == 0
                icon.tap()
            else:
                icon.hover()
            tooltip.wait_for(state="visible")
            expected = page.evaluate("'Resets ' + new Date(1790926320 * 1000).toLocaleString(undefined, {timeZoneName:'short'})")
            assert tooltip.text_content() == expected
            assert page.locator('.cuttle-info').nth(1).get_attribute('data-tooltip') == expected
            assert ('GMT+9' if touch else 'PDT') in expected
            assert icon.get_attribute('aria-describedby') == 'cuttle-shared-tooltip'
            if touch:
                icon.tap()
            else:
                page.keyboard.press('Escape')
            tooltip.wait_for(state="hidden")
            assert icon.get_attribute('aria-describedby') is None
            # Keyboard access works even on devices with no fine hover pointer.
            page.locator('#ordinary').focus()
            page.keyboard.press('Tab')
            icon.focus()
            tooltip.wait_for(state="visible")
            page.keyboard.press('Escape')
            tooltip.wait_for(state="hidden")
            # Live report replacement must not leave an orphaned tooltip visible.
            if touch:
                icon.tap()
            else:
                page.mouse.move(700, 500)
                icon.hover()
            tooltip.wait_for(state="visible")
            page.locator('main').evaluate("el => el.innerHTML = ''")
            tooltip.wait_for(state="hidden")
        finally:
            browser.close()
