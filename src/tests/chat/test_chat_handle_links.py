"""Auto-link CH-000431 / CH-000431-23 in chat message bodies.

Exercises the pure helpers from chat_page.js under Node (skipped if node
is absent). Click → loadChatSession is covered by the navigate helper shape.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"
ACTIVITY_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_activity.js"
CHAT_CSS = Path(__file__).resolve().parents[2] / "web" / "css" / "chat_page.css"
CHAT_HTML = Path(__file__).resolve().parents[2] / "web" / "chat_page.html"

_HELPERS = (
    "toAuthDbSessionId",
    "formatChatDisplayId",
    "escapeHtmlInline",
    "normalizeMdHref",
    "isSafeMdHref",
    "mdLinkChipLabel",
    "mdLinkChipKind",
    "parseChatHandleToken",
    "buildChatHandleHref",
    "renderChatHandleLink",
    "renderMdLinkChip",
    "linkifyChatHandlesInText",
)


def _extract_function(src: str, name: str) -> str:
    start = src.index(f"    function {name}(")
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


_DRIVER = r"""
%s
const assert = require('assert');

// Minimal window for buildChatHandleHref / formatChatDisplayId deps.
global.window = {
  location: { href: 'https://127.0.0.1:8080/chat_page.html?chat=454' },
};

assert.deepStrictEqual(parseChatHandleToken('CH-000431'), {
  sessionId: '431',
  messageIndex: null,
  display: 'CH-000431',
});
assert.deepStrictEqual(parseChatHandleToken('ch-431-9'), {
  sessionId: '431',
  messageIndex: 9,
  display: 'CH-000431-9',
});
assert.strictEqual(parseChatHandleToken('CH-000431-0'), null);
assert.strictEqual(parseChatHandleToken('not-a-handle'), null);
assert.strictEqual(toAuthDbSessionId('CH-000431-23'), '431');
assert.strictEqual(toAuthDbSessionId('CH-000431'), '431');

const link = renderChatHandleLink('CH-000431');
assert.ok(link.includes('class="chat-handle-link"'), link);
assert.ok(link.includes('data-chat-handle="CH-000431"'), link);
assert.ok(link.includes('chat=431'), link);

const msgLink = renderChatHandleLink('CH-000431-12');
assert.ok(msgLink.includes('data-chat-handle="CH-000431-12"'), msgLink);
assert.ok(msgLink.includes('msg=12'), msgLink);

const chips = [];
const out = linkifyChatHandlesInText(
  'See CH-000431 and also `CH-000099` plus CH-000431-3.',
  chips
);
assert.ok(out.includes('{{CUTTLE_LINK_0}}'), out);
assert.ok(out.includes('{{CUTTLE_LINK_1}}'), out);
assert.ok(out.includes('`CH-000099`'), out);
assert.strictEqual(chips.length, 2);
assert.ok(chips[0].includes('CH-000431'));
assert.ok(chips[1].includes('CH-000431-3'));
assert.ok(!chips.some((c) => c.includes('CH-000099')));

// Markdown [CH-…](file://…) must still become a chat-handle badge, not a file chip.
const mdFileWrapped = renderMdLinkChip('CH-000405', 'file:///C:/Projects/Cuttle');
assert.ok(mdFileWrapped.includes('class="chat-handle-link"'), mdFileWrapped);
assert.ok(mdFileWrapped.includes('data-chat-handle="CH-000405"'), mdFileWrapped);
assert.ok(!mdFileWrapped.includes('md-link-chip'), mdFileWrapped);
assert.ok(mdFileWrapped.includes('chat=405'), mdFileWrapped);

const mdBareHref = renderMdLinkChip('see prior', 'CH-000405-12');
assert.ok(mdBareHref.includes('data-chat-handle="CH-000405-12"'), mdBareHref);

console.log('chat-handle-links-ok');
"""


@node_only
def test_chat_handle_link_helpers():
    src = CHAT_JS.read_text(encoding="utf-8")
    helpers = "\n".join(_extract_function(src, name) for name in _HELPERS)
    # Phase 3 Slice 4: the id helpers are thin adapters over chat_activity.js;
    # requiring the module resolves the CuttleChatActivity global they call.
    driver = "require(%s);\n" % json.dumps(str(ACTIVITY_JS)) + (_DRIVER % helpers)
    proc = subprocess.run(
        ["node", "-e", driver],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "chat-handle-links-ok" in proc.stdout


def test_chat_handle_link_css_and_wiring_present():
    js = CHAT_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert "chat-handle-link" in css
    assert "bindChatHandleLinkClicks" in js
    assert "linkifyChatHandlesInText" in js
    assert "chatPageNavigateToChatHandle" in js
    assert "chatHandleLinks" in html or "chat_page.js?v=" in html
    # Bulk session paint must not race CH-…-N jumps with bottom autoScroll.
    assert "skipScroll: true" in js
    assert "_suppressAutoScrollUntil" in js
    assert "consumePendingMessageJump" in js
    assert "queueMessageJump" in js
