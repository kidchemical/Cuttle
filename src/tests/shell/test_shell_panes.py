"""Unit tests for split-pane ordinal parsing / snapshot helpers."""
import importlib
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_ordinal_pane_refs():
    # Import module lazily — Flask app side effects are fine for unit helpers
    from api import web_chat_api as w

    assert w._ordinal_pane_refs('read 1st pane') == [1]
    assert w._ordinal_pane_refs('read the first pane') == [1]
    assert w._ordinal_pane_refs('whats in pane 2') == [2]
    assert w._ordinal_pane_refs('compare 2nd pane and pane 3') == [2, 3]
    assert w._ordinal_pane_refs('right pane') == [-1]
    assert w._ordinal_pane_refs('all panes please') == ['all']
    assert w._ordinal_pane_refs('no mention here') == []


def test_parse_chat_id_from_page():
    from api import web_chat_api as w

    assert w._parse_chat_id_from_page('/chat_page.html?chat=42') == '42'
    assert w._parse_chat_id_from_page('/chat_page.html?session=99') == '99'
    assert w._parse_chat_id_from_page('/settings_page.html') is None
    assert w._parse_chat_id_from_page('') is None


def test_shell_orientation_normalize():
    from api import web_chat_api as w

    assert w._normalize_shell_orientation(None) == 'horizontal'
    assert w._normalize_shell_orientation('horizontal') == 'horizontal'
    assert w._normalize_shell_orientation('VERTICAL') == 'vertical'
    assert w._normalize_shell_orientation('sideways') == 'horizontal'


def test_shell_panes_snapshot_empty():
    from api import web_chat_api as w

    w._shell_pane_layout = {'version': 1, 'orientation': 'horizontal', 'columns': [], 'updated_at': None}
    assert w._shell_panes_snapshot() == []

    w._shell_pane_layout = {
        'version': 1,
        'orientation': 'vertical',
        'columns': [
            {'page': '/chat_page.html?chat=10', 'flex': ''},
            {'page': '/chat_page.html?chat=20', 'flex': ''},
            {'page': '/settings_page.html', 'flex': ''},
        ],
        'updated_at': 1.0,
    }
    panes = w._shell_panes_snapshot()
    assert len(panes) == 3
    assert panes[0]['pane'] == 1 and panes[0]['session_id'] == '10'
    assert panes[1]['pane'] == 2 and panes[1]['session_id'] == '20'
    assert panes[2]['kind'] == 'other' and panes[2]['session_id'] is None
