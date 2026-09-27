"""Window-focus send-keys lived on retired core.tool_manager."""

import pytest


def test_window_focus_retired():
    pytest.skip(
        "Cursor IDE window focus is retired with core.tool_manager; "
        "archive: docs/archive/deprecated-tool-manager/"
    )
