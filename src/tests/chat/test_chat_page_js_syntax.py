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


def test_save_chat_session_keep_search_moved_to_owner():
    """3B step 2 moved saveChatSession (and its keep-search reload anchor) into
    chat_session.js; the page keeps a delegating call site."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = (CHAT_JS.parent / "chat_session.js").read_text(encoding="utf-8")
    assert "// Reload chat history to update sidebar (keep active search/filters)" in owner
    assert "h.reloadHistoryListKeepingSearch();" in owner
    assert "return sessionLifecycle().saveChatSession(message, role, opts);" in text


def test_load_chat_session_moved_to_owner():
    """3B step 3 moved loadChatSession into chat_session.js (CuttleChatSession
    .createLoader); the page keeps a thin delegating call site."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = (CHAT_JS.parent / "chat_session.js").read_text(encoding="utf-8")
    assert "function createLoader(session, host) {" in owner
    assert "async function loadChatSession(sessionId, opts = {})" in owner
    assert "return { loadChatSession: loadChatSession };" in owner
    assert "createLoader: createLoader" in owner
    assert "return sessionLoader().loadChatSession(sessionId, opts);" in text


def test_chat_page_html_loads_busted_script():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert 'src="/js/chat/chat_page.js?v=' in html
