"""Shell pane pause must not persist a fake Stop notice.

Opening Jobs (or any other page) in a split pane posts ``cuttle-pause-streams``
to every chat iframe so Electron can swap frames without freezing. That aborts
the local SSE only. The server run continues, so the transcript must not gain
"⏹ Stopped generating."
"""

from __future__ import annotations

from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"


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
    brace = src.find("{", i)
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


def test_shell_pause_suppresses_stop_notice_and_keeps_sync():
    body = _fn_body(_src(), "function releaseLocalStreamForShellPause")
    # Owned transition (Slice 9B): pause marks detached, never a user stop.
    assert "CuttleStopState.markStreamDetached(stopState)" in body
    assert "ensureGenerationStopNotice" not in body
    assert "CuttleStopState.requestStop" not in body
    # Sibling pane stays on screen — keep polling for the real reply.
    assert "startMessageSync()" in body
    # Only the iframe that is actually going away drops message sync.
    assert "if (teardown)" in body
    assert "stopMessageSync()" in body


def test_pause_handler_uses_release_helper():
    src = _src()
    assert "releaseLocalStreamForShellPause(e.data.type === 'cuttle-pane-teardown')" in src


def test_stopped_generating_only_from_explicit_stop():
    src = _src()
    needle = "ensureGenerationStopNotice('⏹ Stopped generating.')"
    assert src.count(needle) == 1
    stop = _fn_body(src, "async function stopGenerating")
    assert needle in stop
