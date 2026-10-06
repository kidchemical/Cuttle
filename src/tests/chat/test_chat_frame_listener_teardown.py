"""Chat iframes must not leave listeners on the app-shell window.

The shell outlives every chat iframe. A listener a frame adds to the parent
window/document keeps that frame's whole unloaded document alive, so every
chat switch leaked a full chat page until the Electron renderer hit V8 OOM.
Parent-side registrations must carry ``parentListenersAbort.signal``.
"""

from __future__ import annotations

import re
from pathlib import Path

CHAT_PAGE = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"

# Targets that resolve to the app-shell window or document from inside the frame.
PARENT_TARGET = re.compile(
    r"(window\.parent(?:\.\w+)*|hostDoc|lightboxHostWindow\(\)|lightboxHostDocument\(\))"
    r"\.addEventListener\("
)


def test_parent_listeners_are_torn_down_with_the_frame():
    lines = CHAT_PAGE.read_text(encoding="utf-8").splitlines()
    offenders = []
    for idx, line in enumerate(lines):
        if not PARENT_TARGET.search(line):
            continue
        call = "\n".join(lines[idx:idx + 3])
        if not re.search(r"parentListenersAbort\.signal|\b(hostOpts|parentOpts)\)", call):
            offenders.append(f"{idx + 1}: {line.strip()}")
    assert not offenders, "parent-window listeners without teardown signal:\n" + "\n".join(offenders)


def test_teardown_signal_aborts_on_pagehide():
    src = CHAT_PAGE.read_text(encoding="utf-8")
    assert "const parentListenersAbort = new AbortController();" in src
    assert re.search(
        r"addEventListener\('pagehide', event => \{\s*if \(!event\.persisted\) parentListenersAbort\.abort\(\);",
        src,
    )
