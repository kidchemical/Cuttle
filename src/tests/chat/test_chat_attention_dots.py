"""History / title attention dots: error, unread, queued, paused."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CHAT_JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "chat/chat_page.js"
CHAT_CSS = Path(__file__).resolve().parents[3] / "src" / "web" / "css" / "chat_page.css"
CHAT_HTML = Path(__file__).resolve().parents[3] / "src" / "web" / "chat_page.html"
ACTIVITY_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_activity.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@node_only
def test_attention_kind_priority_order():
    # Phase 3 Slice 4: the priority chain lives in chat_activity.js and is
    # required directly (no source slicing — slicing is what rotted the
    # slash/project suites). Priority: error > unread > queued > paused.
    driver = (
        "const A = require(%s);\n"
        "const out = {};\n"
        "const base = { sessionId: '2', currentSessionId: '1', backgrounded: false };\n"
        "out.err = A.sessionHistoryAttentionKind(Object.assign({}, base, {\n"
        "  prefs: { hasUnread: true, unreadIsError: true }, sessionObj: null,\n"
        "  queue: [{ id: 'a' }] }));\n"
        "out.unread = A.sessionHistoryAttentionKind(Object.assign({}, base, {\n"
        "  prefs: { hasUnread: true }, sessionObj: null, queue: [{ id: 'a' }] }));\n"
        "out.queued = A.sessionHistoryAttentionKind(Object.assign({}, base, {\n"
        "  prefs: {}, sessionObj: null, queue: [{ id: 'a' }] }));\n"
        "out.paused = A.sessionHistoryAttentionKind(Object.assign({}, base, {\n"
        "  prefs: {}, sessionObj: null, queue: [{ id: 'a', paused: true }] }));\n"
        "out.none = A.sessionHistoryAttentionKind(Object.assign({}, base, {\n"
        "  prefs: {}, sessionObj: null, queue: [] }));\n"
        "process.stdout.write(JSON.stringify(out));\n" % json.dumps(str(ACTIVITY_JS))
    )
    proc = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res == {"err": "error", "unread": "unread", "queued": "queued",
                  "paused": "paused", "none": ""}


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
