"""Pipeline filesystem/shell managers are archived with deprecated-os-tools."""

import pytest


def test_filesystem_tools_retired():
    pytest.skip("tools.filesystem archived; guest agent CLIs own the workspace")


def test_shell_tools_retired():
    pytest.skip("tools.shell archived; guest agent CLIs own the workspace")
