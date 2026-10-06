"""Spaces tabs mirror the five chat activity states (one dot per space)."""

from __future__ import annotations

from pathlib import Path

SHELL_JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "shell/app_shell.js"
SHELL_CSS = Path(__file__).resolve().parents[3] / "src" / "web" / "css" / "app_shell.css"
CHAT_JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "chat/chat_page.js"


def test_space_activity_priority_order():
    # Aggregation owner is spaces_activity.js (Phase 1); the shell delegates.
    mod = (Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "spaces" / "spaces_activity.js").read_text(encoding="utf-8")
    assert "const RANK = { input: 6, running: 5, error: 4, unread: 3, queued: 2, paused: 1 }" in mod
    # Running is evaluated before (and outranks) every dot state.
    agg = mod.split("function selectSpaceActivity(", 1)[1].split(
        "const api = {", 1
    )[0]
    assert "hit.running" in agg
    assert agg.index("best = 'running'") < agg.index("RANK[kind]")
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "CuttleSpaces.selectSpaceActivity(ids, lookupSpaceSessionActivity," in src
    assert "const SPACE_ACTIVITY_RANK" not in src


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
    assert "CuttleSpaces.notePushSnapshot(" in shell
    # Frames push only the chats they own (stale history-row spinners for
    # other chats used to re-arm space tabs after the poll cleared them).
    snap = chat.split("function collectChatActivitySnapshot()", 1)[1].split(
        "function scheduleChatActivityBroadcast()", 1
    )[0]
    assert ".chat-history-item" not in snap
    assert "owned:" in snap


def test_background_finish_marks_unread_and_queue_strings_parse():
    shell = SHELL_JS.read_text(encoding="utf-8")
    assert "markFinishedBackgroundChatsUnread(CuttleSpaces.noteServerSnapshot(" in shell
    # followup_queue is a TEXT column — the sessions list returns a JSON string.
    assert "parseSpaceFollowupQueue(" in shell


def test_space_activity_css_matches_chat_palette():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".shell-space-activity.is-input" in css
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
