"""The autouse guard that stops tests from launching real agent CLIs."""

from __future__ import annotations

from tests.conftest import _vendor_cli_in_argv

BUNDLE = "/home/u/.local/share/cursor-agent/versions/2026.10.22/"


def test_blocks_vendor_clis_by_binary_or_bundle_folder():
    assert _vendor_cli_in_argv(["codex", "exec"]) == "codex"
    assert _vendor_cli_in_argv([BUNDLE + "node", BUNDLE + "index.js", "-p"]) == "cursor-agent"


def test_allows_tools_bundled_next_to_an_agent_cli():
    assert _vendor_cli_in_argv([BUNDLE + "rg", "--json", "Hello"]) == ""
    assert _vendor_cli_in_argv([BUNDLE + "node", "-e", "1"]) == ""
