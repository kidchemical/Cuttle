"""CH-000444: New Chat must not show the previous chat's agent activity.

Phone repro (Capacitor / app shell): start a turn in chat A → New Chat → send in
chat B → typing/activity line shows chat A's live status.

Fixed in ``chat_page.js`` via ``chatNavGeneration`` / ``turnNavGen``, gated
status paints, and leaving ``suppressStreamAbortUi`` true across nav detach.
"""

from __future__ import annotations

import re
from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"


def _src() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    """Return the brace body of a function whose header starts with ``name``.

    ``name`` is the prefix through the function identifier, e.g.
    ``async function collectPendingResult`` — not the ``(opts = {})`` part,
    so default-arg braces cannot be mistaken for the body.
    """
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


def _sse_session_arm(src: str) -> str:
    """The SSE `session` arm inside fetchChatPayload's stream loop.

    Event classification is owned by chat_pending_result.js
    (``classifyStreamEvent``) and the per-event paint/final plan by
    ``planStreamEvent``; the page keeps only the session arm (it owns
    canonicalization and the turn-guard adopt gate).
    """
    assert "CuttleChatPendingResult.classifyStreamEvent(" in src
    session_m = re.search(
        r"if \(streamEv\.kind === 'session'\) \{[\s\S]*?continue;",
        src,
    )
    assert session_m, "SSE session arm not found"
    return session_m.group(0)


def test_nav_generation_token_exists():
    src = _src()
    # Token source owned by chat_turn_guard.js (Slice 9A); the page holds
    # the single instance and calls capture/bump/stale predicates.
    assert "CuttleTurnGuard.createGeneration()" in src
    detach = _fn_body(src, "function detachLocalGenerationForNavigation")
    assert "CuttleTurnGuard.bump(turnGeneration)" in detach
    assert "CuttleTurnGuard.capture(turnGeneration)" in src
    assert "CuttleTurnGuard.isStale(" in src


def test_sse_status_paint_gated_by_turn_view():
    """Live status from an in-flight SSE must not paint after New Chat."""
    src = _src()
    # The paint decision moved to the owner (planStreamEvent): the page
    # feeds it the turn's paint gate and applies the returned status text.
    assert "CuttleChatPendingResult.planStreamEvent(" in src
    assert "canPaint: canPaintTurnHere()" in src
    assert "updateTypingStatus(streamPlan.paintStatus)" in src


def test_pending_status_poll_gated_by_turn_view():
    """Detached pending waiter must not write typing status into another chat."""
    body = _fn_body(_src(), "async function collectPendingResult")
    assert "opts.navGen" in body or "turnNavGen" in body
    ungated = []
    for m in re.finditer(r"updateTypingStatus\([^)]*\)", body):
        start = max(0, m.start() - 160)
        prelude = body[start : m.start()]
        if "canPaintTurnHere()" not in prelude and "isViewingSession(" not in prelude:
            ungated.append(m.group(0))
    assert body.count("updateTypingStatus(") >= 1, "collectPendingResult should paint status"
    assert not ungated, f"ungated status paints in collectPendingResult: {ungated}"


def test_detach_does_not_reenable_orphan_pending_waiter():
    """New Chat must keep suppressStreamAbortUi true for the aborted turn."""
    body = _fn_body(_src(), "function detachLocalGenerationForNavigation")
    # Owned transition (Slice 9B); detach marks detached, never lifts it.
    assert "CuttleStopState.markStreamDetached(stopState)" in body
    assert "CuttleStopState.clearAbortSuppression" not in body


def test_sse_session_adopt_not_from_zombie_when_welcome():
    """A background turn must not adopt into the New Chat welcome splash."""
    session = _sse_session_arm(_src())
    assert "adoptChatSessionId(" in session
    # Zombie adopt gate now lives in the owner (CuttleTurnGuard
    # .shouldAdoptStreamSession); the page feeds it the nav generation and
    # the live session snapshot. Same contract: a turn from before the
    # navigation bump never adopts into the new UI (behavior pinned in
    # test_chat_turn_guard.py::test_stream_session_adopt_gate).
    assert "CuttleTurnGuard.shouldAdoptStreamSession(" in session
    assert "turnNavGen, turnGeneration" in session
    assert "currentSessionId: session.current()" in session
