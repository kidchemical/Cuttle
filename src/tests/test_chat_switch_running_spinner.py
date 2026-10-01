"""Switching away from a running chat must not flash the purple spinner.

Repro: chat A is generating → open idle chat B → title + history briefly show
the spinning activity wheel until the next live-status / sessions poll.

Fix in chat_page.js:
- destination defaults to idle on switch (cleared until one-shot live-status)
- sessions.generating must not re-arm the *open* chat
- detach keeps the left chat's history spinner (server still running)
- poll intervals unchanged
"""

from __future__ import annotations

from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
MOD_GEN = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_generation.js"


def _src() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    start = src.find(name)
    assert start >= 0, f"missing {name!r}"
    paren = src.find("(", start + len(name))
    assert paren >= 0, f"no '(' after {name!r}"
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
    assert brace >= 0, f"no body '{{' for {name!r}"
    depth = 0
    j = brace
    while j < len(src):
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body = src[brace + 1 : j]
                assert body.strip(), f"empty body for {name!r}"
                return body
        j += 1
    raise AssertionError(f"unbalanced braces for {name!r}")


def test_detach_keeps_left_chat_history_spinner():
    """Leaving a running chat must not clear its history spinner."""
    body = _fn_body(_src(), "function detachLocalGenerationForNavigation")
    assert "keepRunningId" in body or "setHistorySessionRunning(keepRunningId, true)" in body
    assert "endLocalGeneration()" not in body
    assert "setHistorySessionRunning(keepRunningId, true)" in body


def test_load_session_clears_destination_spinner_until_live_status():
    """Opened chat defaults idle; stale runningSessionIds must not flash title."""
    body = _fn_body(_src(), "async function loadChatSession")
    assert "switchingAway" in body
    assert "hubLiveStatusCache = null" in body
    assert "setHistorySessionRunning(sessionId, false)" in body
    assert "fetchChatLiveStatus" in body
    # One-shot sessions apply is fine; must not bump the poll interval constant.
    assert "HISTORY_GENERATING_POLL_MS =" not in body


def test_sessions_generating_does_not_rearm_open_chat():
    """sessions.generating must not re-arm the open chat right after a switch."""
    src = _src()
    assert "let deferOpenChatGeneratingFromSessions = false" in src
    body = _fn_body(src, "function applyGeneratingFlagsFromSessions")
    assert "deferOpenChatGeneratingFromSessions" in body
    load = _fn_body(src, "async function loadChatSession")
    assert "deferOpenChatGeneratingFromSessions = true" in load
    assert "deferOpenChatGeneratingFromSessions = false" in load


def test_poll_intervals_unchanged():
    src = _src()
    assert "const HISTORY_GENERATING_POLL_MS = 12000" in src
    # Sync cadence intervals live in the owned generation module (Slice 9E);
    # the page only delegates the delay decision.
    mod = MOD_GEN.read_text(encoding="utf-8")
    assert "active: 5000" in mod
    assert "CuttleChatGeneration.decideSyncDelayMs(" in src
    assert "const MESSAGE_SYNC_ACTIVE_MS = 5000" not in src
