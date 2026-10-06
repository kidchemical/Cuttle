"""chat_page.js must parse — a SyntaxError blanks the entire chat UI.

Regression: commit 73341e4 dropped ``function _historyActionIcons() {`` while
rewiring history reload, leaving a bare ``return {…}`` then ``let`` → browsers
reported ``Unexpected identifier 'let'`` and the page never loaded (phone +
desktop).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"
CHAT_HTML = Path(__file__).resolve().parents[2] / "web" / "chat_page.html"


@node_only
def test_chat_page_js_parses():
    proc = subprocess.run(
        ["node", "--check", str(CHAT_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        "chat_page.js failed node --check (UI will not load):\n"
        + (proc.stderr or proc.stdout or "(no output)")
    )


def test_history_action_icons_function_present():
    """Guard the exact deletion that broke parse: header must precede its body."""
    text = CHAT_JS.read_text(encoding="utf-8")
    assert "function _historyActionIcons() {" in text
    # Anchor on saveChatSession's keep-search call (the edit that ate the header).
    marker = (
        "// Reload chat history to update sidebar (keep active search/filters)\n"
        "        reloadHistoryListKeepingSearch();\n"
        "    }\n"
    )
    idx = text.find(marker)
    assert idx >= 0, "saveChatSession keep-search reload marker missing"
    after = text[idx + len(marker) : idx + len(marker) + 120]
    assert after.lstrip().startswith("function _historyActionIcons() {"), (
        "orphaned _historyActionIcons body — function header missing after "
        "saveChatSession (browser SyntaxError: Unexpected identifier 'let')"
    )


def test_chat_page_html_loads_busted_script():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert 'src="/js/chat/chat_page.js?v=' in html
