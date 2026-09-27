"""History / title attention dots: error, unread, queued, paused."""

from __future__ import annotations

from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[2] / "src" / "web" / "js" / "chat_page.js"
CHAT_CSS = Path(__file__).resolve().parents[2] / "src" / "web" / "css" / "chat_page.css"
CHAT_HTML = Path(__file__).resolve().parents[2] / "src" / "web" / "chat_page.html"


def test_attention_kind_priority_order():
    src = CHAT_JS.read_text(encoding="utf-8")
    fn = src.split("function sessionHistoryAttentionKind(", 1)[1].split(
        "\n    function ", 1
    )[0]
    assert "sessionHasUnreadError" in fn
    assert "sessionHasUnread" in fn
    assert "sessionHasActiveFollowup" in fn
    assert "sessionHasPausedFollowup" in fn
    # Priority: error before unread before queued before paused
    assert fn.index("sessionHasUnreadError") < fn.index("return 'unread'")
    assert fn.index("return 'queued'") < fn.index("return 'paused'")


def test_active_queue_is_amber_paused_is_yellow():
    css = CHAT_CSS.read_text(encoding="utf-8")
    # Amber for active queue (CH-000439-14 legend extended)
    assert ".history-queued-icon" in css
    assert ".chat-session-queued-icon" in css
    queued = css.split(".history-queued-icon,", 1)[1].split("@keyframes", 1)[0]
    assert "#f59e0b" in queued
    # Yellow for paused (was amber)
    paused = css.split(".history-paused-icon,", 1)[1].split("@keyframes", 1)[0]
    assert "#eab308" in paused


def test_unread_error_is_red():
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert ".history-unread-icon.is-error" in css
    assert "#ef4444" in css
    assert "chat-unread-error-glow" in css
    assert ".chat-jump-bottom.has-unseen-error" in css
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert 'id="chatSessionQueuedIcon"' in html


def test_notify_passes_error_flag():
    src = CHAT_JS.read_text(encoding="utf-8")
    notify = src.split("function notifyAssistantResponseReady(", 1)[1].split(
        "\n    function historyUnreadIconHTML", 1
    )[0]
    assert "opts.isError" in notify
    assert "markChatSessionUnread(sid, { isError })" in notify
    assert "noteFinalizedAssistantAttention({ isError })" in notify
