"""Archived badge in the chat title bar.

When the open chat is archived, a badge sits before the session title up
top. Pins the wiring: badge element order in the HTML, its CSS, the toggle
driven by the archived-marks set, and the re-sync after the archived list
loads (marks arrive after the title renders on page load).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_HTML = REPO_ROOT / "src" / "web" / "chat_page.html"
CHAT_CSS = REPO_ROOT / "src" / "web" / "css" / "chat_page.css"
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_page.js"


def test_badge_element_sits_before_title_text():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert 'id="chatSessionArchivedBadge"' in html
    badge_pos = html.index('id="chatSessionArchivedBadge"')
    title_pos = html.index('id="chatSessionTitleText"')
    assert badge_pos < title_pos
    badge_tag = html[html.rindex("<span", 0, badge_pos):html.index("</span>", badge_pos)]
    assert "hidden" in badge_tag
    assert "Archived" in badge_tag


def test_badge_css_hides_with_hidden_attribute():
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert ".chat-session-archived-badge" in css
    assert ".chat-session-archived-badge[hidden]" in css


def test_title_update_drives_badge_from_archived_marks():
    src = CHAT_JS.read_text(encoding="utf-8")
    toggle = src.split("function updateChatSessionArchivedBadge(", 1)[1].split(
        "\n    }\n", 1
    )[0]
    assert "chatSessionArchivedBadge" in toggle
    assert "isChatSessionArchived(sessionId)" in toggle
    title_fn = src.split("function updateChatSessionTitle(", 1)[1].split(
        "\n    function updateChatSessionStarButton(", 1
    )[0]
    # Both branches: empty session hides it, open session re-evaluates it.
    assert title_fn.count("updateChatSessionArchivedBadge(") >= 2


def test_archived_list_refresh_resyncs_title_badge():
    src = CHAT_JS.read_text(encoding="utf-8")
    refresh = src.split("async function refreshArchivedSessions()", 1)[1].split(
        "\n    function renderArchivedSection()", 1
    )[0]
    assert "updateChatSessionTitle(currentSessionId)" in refresh
