"""Rail gizmos dock on a visible rail, never a parked pane's hidden one."""
import json

from .test_app_shell_layout import ShellWorld, _open, _load_shell, _electron_titlebar
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class GizmoWorld(ShellWorld):
    def handle(self, route):
        if route.request.url.split('?')[0].endswith('/api/gizmos'):
            return route.fulfill(status=200, content_type='application/json', body=json.dumps({
                'success': True, 'revision': 1, 'types': [],
                'gizmos': [{'id': 'g1', 'type': 'usage_meter', 'title': 'Budget', 'config': {},
                            'placement': {'dock': 'rail', 'order': 0}}]}))
        return super().handle(route)


def _leaf(lid, **extra):
    return {'type': 'leaf', 'id': lid, 'page': '/chat_page.html?new=1', 'flex': '1 1 0%',
            'railCollapsed': False, **extra}


def test_rail_dock_skips_parked_top_cell_in_other_space(browser, static_server):
    context, page, errors, _ = _open(browser, static_server, GizmoWorld())
    # Second space: a vertical stack whose top cell is parked (its rail stays
    # in layout as a hidden strip, leftmost and topmost).
    stacked = {'type': 'group', 'id': 'grp_h', 'orientation': 'horizontal', 'flex': '', 'children': [
        {'type': 'group', 'id': 'grp_v', 'orientation': 'vertical', 'flex': '1 1 0%', 'children': [
            _leaf('leaf_top', flex='0 0 auto', collapsed=True, restoreEdge='trailing'),
            _leaf('leaf_bottom'),
        ]},
        _leaf('leaf_right'),
    ]}
    state = {'active': 'a', 'groups': [], 'spaces': [
        {'id': 'a', 'name': 'A', 'root': _leaf('leaf_a', flex='')},
        {'id': 'b', 'name': 'B', 'root': stacked},
    ]}
    context.add_init_script('localStorage.setItem("shell_spaces_v1", ' + json.dumps(json.dumps(state)) + ');')
    try:
        _load_shell(page, static_server)
        _electron_titlebar(page)
        page.wait_for_selector('#shellGizmoDockRail .shell-gizmo', state='visible')
        page.evaluate("switchSpace('b')")
        page.wait_for_function("""() => {
            const d = document.getElementById('shellGizmoDockRail');
            const col = d && d.closest('.split-column');
            return !!(col && !col.classList.contains('pane-collapsed') && d.querySelector('.shell-gizmo'));
        }""", timeout=6000)
        assert page.locator('#shellGizmoDockRail .shell-gizmo').is_visible()
        assert errors == []
    finally:
        context.close()
