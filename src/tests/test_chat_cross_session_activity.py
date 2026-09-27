"""CH-000444: New Chat must not show the previous chat's agent activity.

Phone repro (Capacitor / app shell): start a turn in chat A → New Chat → send in
chat B → typing/activity line shows chat A's live status.

Fixed in ``chat_page.js`` via ``chatNavGeneration`` / ``turnNavGen``, gated
status paints, and leaving ``suppressStreamAbortUi`` true across nav detach.
"""

from __future__ import annotations

import re
from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"


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


def _sse_event_arms(src: str) -> dict[str, str]:
    """Map SSE ``ev.type === '…'`` arms inside fetchChatPayload's stream loop."""
    anchor = src.find("ev.type === 'status' && ev.message")
    assert anchor >= 0
    window = src[anchor - 200 : anchor + 3500]
    arms: dict[str, str] = {}
    status_m = re.search(
        r"if \(ev\.type === 'status' && ev\.message\) \{[\s\S]*?(?=\} else if \(ev\.type ===)",
        window,
    )
    assert status_m, "SSE status arm not found"
    arms["status"] = status_m.group(0)
    session_m = re.search(
        r"else if \(ev\.type === 'session' && ev\.session_id != null\) \{[\s\S]*?(?=\} else if \(ev\.type ===)",
        window,
    )
    assert session_m, "SSE session arm not found"
    arms["session"] = session_m.group(0)
    return arms


def test_nav_generation_token_exists():
    src = _src()
    assert "let chatNavGeneration = 0" in src
    detach = _fn_body(src, "function detachLocalGenerationForNavigation")
    assert "chatNavGeneration += 1" in detach
    assert "const turnNavGen = chatNavGeneration" in src
    assert "turnNavGen !== chatNavGeneration" in src


def test_sse_status_updates_gated_by_turn_view():
    """Live status from an in-flight SSE must not paint after New Chat."""
    arms = _sse_event_arms(_src())
    status = arms["status"]
    assert "updateTypingStatus(" in status
    assert "canPaintTurnHere()" in status


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
    assert "suppressStreamAbortUi = true" in body
    assert "suppressStreamAbortUi = false" not in body


def test_sse_session_adopt_not_from_zombie_when_welcome():
    """A background turn must not adopt into the New Chat welcome splash."""
    arms = _sse_event_arms(_src())
    session = arms["session"]
    assert "adoptChatSessionId(" in session
    assert "turnNavGen === chatNavGeneration" in session
    # Welcome null path may remain, but only under the nav-gen gate.
    if "currentSessionId == null" in session:
        null_idx = session.index("currentSessionId == null")
        window = session[max(0, null_idx - 200) : null_idx + 40]
        assert "turnNavGen === chatNavGeneration" in window or "turnNavGen" in session
