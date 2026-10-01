"""Switching chats mid-turn must not fire Reply ready / green unread early.

Repro: send in chat A → open chat B while the agent is still working → green
dot + "Reply ready" toast, but A is still thinking.

Root cause: after detach cleared ``inFlightUserMessage``,
``assistantElAfterInFlightUser`` treated any assistant in the (new) DOM as
this turn's reply (``seenUser = !wantUser``), and the pending-miss path
returned ``success: true`` → ``notifyAssistantResponseReady``.
"""

from __future__ import annotations

from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
MOD_PR = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_pending_result.js"


def _src() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    start = src.find(name)
    assert start >= 0, f"missing {name!r}"
    paren = src.find("(", start + len(name))
    assert paren >= 0
    depth = 0
    i = paren
    while i < len(src):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        raise AssertionError(f"unbalanced parens for {name!r}")
    brace = src.find("{", i)
    assert brace >= 0
    depth = 0
    j = brace
    while j < len(src):
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[brace + 1 : j]
        j += 1
    raise AssertionError(f"unbalanced braces for {name!r}")


def test_assistant_el_requires_in_flight_user():
    body = _fn_body(_src(), "function assistantElAfterInFlightUser")
    assert "if (!wantUser) return null" in body
    assert "let seenUser = !wantUser" not in body
    assert "let seenUser = false" in body


def test_pending_miss_skips_painted_after_nav():
    src = _src()
    assert "CuttleChatPendingResult.recoverAfterStreamDetach(" in src
    assert "isNavAway: () => stopState.abortSuppressed" in src
    mod = MOD_PR.read_text(encoding="utf-8")
    assert "pending-miss-nav" in mod
    assert "detached — no false ready" in mod
    # Detach starts a quiet completion poll instead of an immediate chirp.
    detach = _fn_body(src, "function detachLocalGenerationForNavigation")
    assert "watchDetachedSessionForCompletion(keepRunningId)" in detach


def test_background_notify_requires_nonempty_body():
    src = _src()
    # The !canPaintTurnHere branch must require readyBody, not success||trim.
    idx = src.find("if (!canPaintTurnHere())")
    assert idx >= 0
    window = src[idx : idx + 1200]
    assert "readyBody" in window
    assert "&& readyBody" in window
    assert "(data.success || String(data.response" not in window


def test_cursor_stream_switch_does_not_chirp_mid_run():
    body = _fn_body(_src(), "async function processCursorCommandStreaming")
    assert "notifyAssistantResponseReady(boundStreamSessionId)" not in body.split(
        "activeEventSource.onmessage", 1
    )[1].split("data.type === 'done'", 1)[0]
    assert "watchDetachedSessionForCompletion(boundStreamSessionId)" in body


def test_detached_watcher_only_notifies_on_pending_body():
    body = _fn_body(_src(), "function watchDetachedSessionForCompletion")
    assert "/api/chat-pending-result" in body
    assert "notifyAssistantResponseReady(sid" in body
    assert "data.pending && data.result" in body
    assert ".trim()" in body
