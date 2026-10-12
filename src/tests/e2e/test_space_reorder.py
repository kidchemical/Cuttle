"""A released space/group must keep exactly the order shown during the drag."""
import json

import pytest

from .test_app_shell_layout import ShellWorld, _open, _load_shell, _electron_titlebar
from .test_shared_diff_modal import browser, static_server  # noqa: F401


@pytest.mark.parametrize('collapsed', [False, True])
@pytest.mark.parametrize('dragged, destination, fraction', [
    ('a', 'd', .8), ('d', 'a', .2),
    ('a', 'g', .2), ('d', 'g', .8),
    ('g', 'a', .2), ('g', 'd', .8),
    ('b', 'a', .2), ('c', 'd', .8), ('c', 'g', .3),
    ('a', 'h', .2), ('d', 'h', .8), ('g', 'h', .8), ('h', 'g', .2),
])
def test_release_preserves_preview(browser, static_server, collapsed, dragged, destination, fraction):
    context, page, errors, _ = _open(browser, static_server, ShellWorld())
    state = {
        'active': 'a',
        'groups': [{'id': 'g', 'name': 'Group', 'color': '#8ab4f8', 'collapsed': collapsed},
                   {'id': 'h', 'name': 'Longer group', 'color': '#f28b82', 'collapsed': collapsed}],
        'spaces': [{'id': 'a', 'name': 'Alpha'},
                   {'id': 'b', 'name': 'Beta', 'groupId': 'g'},
                   {'id': 'c', 'name': 'Gamma', 'groupId': 'g'},
                   {'id': 'd', 'name': 'Delta'}, {'id': 'e', 'name': 'Epsilon'},
                   {'id': 'f', 'name': 'Wide member name', 'groupId': 'h'},
                   {'id': 'j', 'name': 'Short', 'groupId': 'h'}],
    }
    context.add_init_script('localStorage.setItem("shell_spaces_v1", ' + json.dumps(json.dumps(state)) + ');')
    def locate(id, drag=False):
        if id in ('g', 'h'):
            cls = '.shell-space-group' if drag else '.shell-space-group-sleeve'
            return page.locator(f'{cls}[data-group-id="{id}"]')
        return page.locator(f'[data-space-id="{id}"]')
    preview = """() => {
        const order = [], membership = {};
        document.querySelectorAll('#shellSpacesTabs > *').forEach(el => {
            if (el.classList.contains('shell-space-tab')) {
                order.push(el.dataset.spaceId); membership[el.dataset.spaceId] = null;
            } else if (el.classList.contains('is-collapsed')) {
                spacesState.spaces.filter(s => s.groupId === el.dataset.groupId).forEach(s => {
                    order.push(s.id); membership[s.id] = el.dataset.groupId;
                });
            } else {
                el.querySelectorAll('.shell-space-tab').forEach(t => {
                    order.push(t.dataset.spaceId); membership[t.dataset.spaceId] = el.dataset.groupId;
                });
            }
        });
        return {order, membership};
    }"""
    try:
        if collapsed and dragged in ('b', 'c'):
            pytest.skip('Collapsed members have no drag target')
        _load_shell(page, static_server)
        _electron_titlebar(page)
        start = locate(dragged, True).bounding_box()
        end = locate(destination).bounding_box()
        x = end['x'] + end['width'] * fraction
        y = start['y'] + start['height'] / 2
        page.mouse.move(start['x'] + start['width'] / 2, y)
        page.mouse.down()
        page.mouse.move(x, y, steps=12)
        # Small nudges should settle at the displayed slot and then release there.
        page.mouse.move(x + 1, y)
        page.mouse.move(x, y)
        expected = page.evaluate(preview)
        page.mouse.move(x, y)
        page.mouse.move(x, y)
        assert page.evaluate(preview) == expected, 'A stationary pointer must not move the preview slot'
        page.mouse.up()
        actual = page.evaluate("""() => ({
            order: spacesState.spaces.map(s => s.id),
            membership: Object.fromEntries(spacesState.spaces.map(s => [s.id, s.groupId || null]))
        })""")
        assert actual == expected
        saved = page.evaluate("JSON.parse(localStorage.getItem('shell_spaces_v1')).spaces.map(s => s.id)")
        assert saved == expected['order']
        assert not errors
    finally:
        context.close()
