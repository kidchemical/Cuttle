"""Overflow controls against real shell assets and an isolated fake API."""
import json

from .test_app_shell_layout import (
    ShellWorld, _electron_titlebar, _load_shell, _open,
)
from .test_shared_diff_modal import browser, static_server  # noqa: F401


def test_space_tabs_scroll_and_resize(browser, static_server):
    world = ShellWorld()
    context, page, errors, blocked = _open(browser, static_server, world)
    state = {
        "active": "sp0", "groups": [],
        "spaces": [{"id": f"sp{i}", "name": f"Workspace {i + 1}", "root": None}
                   for i in range(12)],
    }
    context.add_init_script(
        "localStorage.setItem('shell_spaces_v1', " + json.dumps(json.dumps(state)) + ");")
    try:
        page.set_viewport_size({"width": 700, "height": 800})
        _load_shell(page, static_server)
        _electron_titlebar(page)
        left = page.locator('#shellSpacesScrollLeft')
        right = page.locator('#shellSpacesScrollRight')
        right.wait_for(state='visible')
        page.wait_for_function("document.getElementById('shellSpacesScrollLeft').disabled")
        assert right.is_enabled()
        assert page.locator('#shellSpacesAdd').is_visible()

        page.locator('#shellSpacesTabs').hover()
        page.mouse.wheel(0, 180)
        page.wait_for_function("document.getElementById('shellSpacesTabs').scrollLeft > 100")
        page.mouse.wheel(0, -1000)
        page.wait_for_function("document.getElementById('shellSpacesTabs').scrollLeft === 0")

        def scroll(button):
            page.evaluate("""() => {
                window.tabScrollEnded = false;
                document.getElementById('shellSpacesTabs').addEventListener(
                    'scrollend', () => { window.tabScrollEnded = true; }, {once: true});
            }""")
            button.click()
            page.wait_for_function('window.tabScrollEnded')

        scroll(right)
        page.wait_for_function("document.getElementById('shellSpacesTabs').scrollLeft > 50")
        assert left.is_enabled()
        assert page.evaluate("spacesState.active") == 'sp0'

        # Keep paging until the last tab is reachable and the right arrow disables.
        for _ in range(10):
            if right.is_disabled():
                break
            scroll(right)
        page.wait_for_function("document.getElementById('shellSpacesScrollRight').disabled")
        page.locator('[data-space-id="sp11"]').click()
        page.wait_for_function("spacesState.active === 'sp11'")

        # Widening hides controls even though they previously consumed strip width.
        page.set_viewport_size({"width": 2400, "height": 800})
        right.wait_for(state='hidden')
        assert left.is_hidden()
        page.set_viewport_size({"width": 700, "height": 800})
        right.wait_for(state='visible')
        page.wait_for_function("document.getElementById('shellSpacesScrollRight').disabled")
        scroll(left)
        page.wait_for_function("!document.getElementById('shellSpacesScrollRight').disabled")
        assert not errors
        assert not [url for url in blocked if '/api/' in url]
    finally:
        context.close()


def test_group_drag_collapse_and_active_tab_shape(browser, static_server):
    context, page, errors, blocked = _open(browser, static_server, ShellWorld())
    state = {
        'active': 'a',
        'groups': [{'id': 'g', 'name': 'Work', 'color': '#8ab4f8', 'collapsed': False}],
        'spaces': [{'id': 'a', 'name': 'First', 'groupId': 'g', 'root': None},
                   {'id': 'b', 'name': 'Second', 'groupId': 'g', 'root': None},
                   {'id': 'c', 'name': 'Other', 'root': None},
                   {'id': 'd', 'name': 'Last', 'root': None}],
    }
    context.add_init_script("if (!localStorage.getItem('shell_spaces_v1')) "
                            "localStorage.setItem('shell_spaces_v1', " + json.dumps(json.dumps(state)) + ");")
    try:
        _load_shell(page, static_server)
        _electron_titlebar(page)
        page.wait_for_function("document.querySelector('.shell-space-tab.is-active') !== null")
        assert page.evaluate("""() => {
            const tab = document.querySelector('.shell-space-tab.is-active');
            const line = getComputedStyle(tab, '::before');
            return Math.abs(tab.getBoundingClientRect().bottom -
                document.getElementById('shellTitlebar').getBoundingClientRect().bottom) < 1.5
                && getComputedStyle(tab).borderBottomWidth === '0px'
                && line.content === '\"\"' && line.height === '2px';
        }""")

        pill = page.locator('.shell-space-group[data-group-id="g"]')
        pill.click()
        assert page.evaluate("spacesState.active") == 'a'
        assert page.locator('[data-space-id="a"]').count() == 0
        assert pill.get_attribute('aria-expanded') == 'false'
        assert 'is-active' in pill.get_attribute('class')
        assert pill.evaluate("""pill => {
            const css = getComputedStyle(pill);
            const line = getComputedStyle(pill, '::after');
            return Math.abs(pill.getBoundingClientRect().bottom -
                document.getElementById('shellTitlebar').getBoundingClientRect().bottom) < 1.5
                && css.borderBottomWidth === '0px' && css.borderBottomLeftRadius === '0px'
                && css.backgroundColor !== 'rgba(0, 0, 0, 0)'
                && line.content === '\"\"' && line.height === '2px';
        }""")

        # Collapsed groups drag as a block without expanding or switching spaces.
        origin = pill.bounding_box()
        last = page.locator('[data-space-id="d"]').bounding_box()
        page.mouse.move(origin['x'] + origin['width'] / 2, origin['y'] + origin['height'] / 2)
        page.mouse.down()
        page.mouse.move(last['x'] + last['width'] - 2, origin['y'] + origin['height'] / 2, steps=12)
        page.mouse.up()
        assert not errors
        assert page.evaluate("spacesState.spaces.map(s => s.id)") == ['c', 'd', 'a', 'b']
        assert page.evaluate("spacesState.groups[0].collapsed") is True
        assert page.evaluate("spacesState.active") == 'a'

        pill.click()
        assert pill.evaluate("pill => getComputedStyle(pill).borderBottomLeftRadius !== '0px'")
        origin = pill.bounding_box()
        first = page.locator('[data-space-id="c"]').bounding_box()
        page.mouse.move(origin['x'] + origin['width'] / 2, origin['y'] + origin['height'] / 2)
        page.mouse.down()
        page.mouse.move(first['x'] + 2, origin['y'] + origin['height'] / 2, steps=12)
        page.mouse.up()
        assert page.evaluate("spacesState.spaces.map(s => s.id)") == ['a', 'b', 'c', 'd']
        assert page.evaluate("spacesState.groups[0].collapsed") is False

        # Cancel restores the preview without committing the order or triggering collapse.
        origin = pill.bounding_box()
        last = page.locator('[data-space-id="d"]').bounding_box()
        page.mouse.move(origin['x'] + 5, origin['y'] + 10)
        page.mouse.down()
        page.mouse.move(last['x'] + last['width'] - 2, origin['y'] + 10, steps=12)
        page.keyboard.press('Escape')
        page.mouse.up()
        assert page.evaluate("spacesState.spaces.map(s => s.id)") == ['a', 'b', 'c', 'd']
        assert page.evaluate("spacesState.groups[0].collapsed") is False
        pill.click()
        page.reload(wait_until='domcontentloaded')
        _electron_titlebar(page)
        assert page.evaluate("spacesState.active") == 'a'
        assert pill.get_attribute('aria-expanded') == 'false'
        assert not errors
        assert not [url for url in blocked if '/api/' in url]
    finally:
        context.close()
