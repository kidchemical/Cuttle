"""Chat iframes must not leave listeners on the app-shell window.

The shell outlives every chat iframe. A listener a frame adds to the parent
window/document keeps that frame's whole unloaded document alive, so every
chat switch leaked a full chat page until the Electron renderer hit V8 OOM.
Parent-side registrations must carry the frame teardown signal: the page's
``parentListenersAbort.signal``, which owners receive as
``hostTeardownSignal`` (chat_media.js mounts the lightbox on the host).
"""

from __future__ import annotations

import re
from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat"
CHAT_PAGE = CHAT_JS / "chat_page.js"
# Every chat-frame source that registers listeners on the shell window/document.
HOST_LISTENER_SOURCES = [CHAT_PAGE, CHAT_JS / "chat_media.js"]

# Targets that resolve to the app-shell window or document from inside the frame.
PARENT_TARGET = re.compile(
    r"(window\.parent(?:\.\w+)*|hostDoc|lightboxHostWindow\(\)|lightboxHostDocument\(\))"
    r"\.addEventListener\("
)


def test_parent_listeners_are_torn_down_with_the_frame():
    offenders = []
    scanned = 0
    for path in HOST_LISTENER_SOURCES:
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if not PARENT_TARGET.search(line):
                continue
            scanned += 1
            call = "\n".join(lines[idx:idx + 3])
            if not re.search(
                r"parentListenersAbort\.signal|hostTeardownSignal|\b(hostOpts|parentOpts)\)", call
            ):
                offenders.append(f"{path.name}:{idx + 1}: {line.strip()}")
    assert scanned >= 5, "host-listener scan found too few registrations; did a source move?"
    assert not offenders, "parent-window listeners without teardown signal:\n" + "\n".join(offenders)


def test_teardown_signal_aborts_on_pagehide():
    src = CHAT_PAGE.read_text(encoding="utf-8")
    assert "const parentListenersAbort = new AbortController();" in src
    assert re.search(
        r"addEventListener\('pagehide', event => \{\s*if \(!event\.persisted\) parentListenersAbort\.abort\(\);",
        src,
    )
