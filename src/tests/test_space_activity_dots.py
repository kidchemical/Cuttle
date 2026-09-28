"""Spaces tabs mirror the five chat activity states (one dot per space)."""

from __future__ import annotations

from pathlib import Path

SHELL_JS = Path(__file__).resolve().parents[2] / "src" / "web" / "js" / "app_shell.js"
SHELL_CSS = Path(__file__).resolve().parents[2] / "src" / "web" / "css" / "app_shell.css"
CHAT_JS = Path(__file__).resolve().parents[2] / "src" / "web" / "js" / "chat_page.js"


def test_space_activity_priority_order():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "const SPACE_ACTIVITY_RANK = { running: 5, error: 4, unread: 3, queued: 2, paused: 1 }" in src
    # Running is evaluated before (and outranks) every dot state.
    agg = src.split("function spaceActivityFor(", 1)[1].split(
        "function syncSpaceActivityTabs(", 1
    )[0]
    assert "hit.running" in agg
    assert agg.index("best = 'running'") < agg.index("SPACE_ACTIVITY_RANK[kind]")


def test_space_tab_renders_activity_dot():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert '<span class="shell-space-activity"' in src
    assert "syncSpaceActivityTabs()" in src


def test_space_activity_covers_inactive_spaces():
    src = SHELL_JS.read_text(encoding="utf-8")
    # Inactive spaces parse their stored layout tree; terminals are excluded.
    assert "flattenLayoutLeaves(space.root" in src
    assert "cuttleChatSessionPrefs" in src
    assert "/api/auth/sessions" in src
    assert "/api/chat-live-status-batch" in src


def test_chat_pushes_activity_snapshot_to_shell():
    chat = CHAT_JS.read_text(encoding="utf-8")
    shell = SHELL_JS.read_text(encoding="utf-8")
    assert "cuttle-chat-activity" in chat
    assert "collectChatActivitySnapshot" in chat
    assert "cuttle-chat-activity" in shell
    assert "noteSpaceSessionActivity" in shell


def test_space_activity_css_matches_chat_palette():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".shell-space-activity.is-unread" in css
    assert ".shell-space-activity.is-error" in css
    assert ".shell-space-activity.is-queued" in css
    assert ".shell-space-activity.is-paused" in css
    assert ".shell-space-activity.is-running" in css
    # Same five colors as the chat history dots.
    assert "#22c55e" in css  # unread green
    assert "#ef4444" in css  # error red
    assert "#f59e0b" in css  # queued amber/orange
    assert "#eab308" in css  # paused yellow
    assert "shell-space-spin" in css
