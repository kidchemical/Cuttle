"""Quote clips (chat_quote_clips.js): pure decisions + the markup contract.

Contracts pinned here:
- the toolbar offers the quick replies and Reply, each quick one canned;
- a clip needs selected text and a reply; quotes are trimmed and capped;
- a clip is sent as a Markdown blockquote followed by its reply, clips ahead
  of the typed text — except typed slash commands, which stay first;
- the toolbar sits above the selection, below when there is no room (or on
  touch), and never leaves the viewport;
- the owner script loads before the page that composes it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
OWNER = WEB / "js" / "chat" / "chat_quote_clips.js"
CHAT_HTML = WEB / "chat_page.html"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _node(script: str) -> None:
    proc = subprocess.run(["node", "-e", script, str(OWNER)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


@node_only
def test_actions_and_clip_rules():
    _node(r"""
const assert = require('assert');
const Q = require(process.argv[1]);
assert.deepStrictEqual(Q.ACTIONS.map((a) => a.id), ['agree', 'disagree', 'explain', 'investigate', 'do', 'reply']);
// Every quick button carries its canned reply; Reply has none (typed).
Q.ACTIONS.forEach((a) => assert.strictEqual(a.reply == null, a.id === 'reply'));
// The toolbar shows Reply first, then the quick replies in order.
assert.deepStrictEqual(Q.toolbarOrder().map((a) => a.id), ['reply', 'agree', 'disagree', 'explain', 'investigate', 'do']);

assert.deepStrictEqual(Q.makeClip('agree', '  use a queue \n'), { action: 'agree', quote: 'use a queue', reply: 'I agree with this.' });
// A canned button ignores typed text; Reply requires it.
assert.strictEqual(Q.makeClip('explain', 'x', 'ignored').reply, 'Explain this in more detail.');
assert.strictEqual(Q.makeClip('reply', 'x', '  '), null);
assert.strictEqual(Q.makeClip('reply', 'x', ' why? ').reply, 'why?');
assert.strictEqual(Q.makeClip('agree', '   '), null);
assert.strictEqual(Q.makeClip('nope', 'x'), null);

assert.strictEqual(Q.normalizeQuote('a  \r\n\r\n\r\n\r\nb c '), 'a\n\nb c');
const long = Q.normalizeQuote('x'.repeat(Q.MAX_QUOTE_CHARS + 50));
assert.strictEqual(long.length, Q.MAX_QUOTE_CHARS + 1);
assert.ok(long.endsWith('…'));
assert.strictEqual(Q.excerpt('one\n two   three', 9), 'one two…');
""")


@node_only
def test_clips_compose_into_the_message():
    _node(r"""
const assert = require('assert');
const Q = require(process.argv[1]);
const a = Q.makeClip('agree', 'first line\n\nsecond line');
const r = Q.makeClip('reply', 'other', 'not sure');
assert.strictEqual(Q.clipText(a), '> first line\n>\n> second line\n\nI agree with this.');
assert.strictEqual(Q.composeBody([], '  hi '), 'hi');
assert.strictEqual(Q.composeBody([a], ''), Q.clipText(a));
assert.strictEqual(Q.composeBody([a, r], 'and ship it'),
    Q.clipText(a) + '\n\n> other\n\nnot sure\n\nand ship it');
// A typed slash command must stay at the front to parse.
assert.strictEqual(Q.composeBody([r], '/restart'), '/restart\n\n> other\n\nnot sure');
""")


@node_only
def test_queued_preview_is_compact():
    _node(r"""
const assert = require('assert');
const Q = require(process.argv[1]);
const esc = (s) => s.replace(/</g, '&lt;');
const body = Q.composeBody([Q.makeClip('agree', 'a <b>\n\nc'), Q.makeClip('reply', 'other', 'why')], 'two\nlines');
// One line per quote, one block per paragraph, no blank lines between them.
assert.strictEqual(Q.previewHtml(body, esc),
    '<span class="quote-preview-quote">a &lt;b> c</span><span class="quote-preview-text">I agree with this.</span>'
    + '<span class="quote-preview-quote">other</span><span class="quote-preview-text">why</span>'
    + '<span class="quote-preview-text">two\nlines</span>');
// Plain paragraphs lose the blank rows between them too.
assert.strictEqual(Q.previewHtml('plain\n\n\ntext', esc),
    '<span class="quote-preview-text">plain</span><span class="quote-preview-text">text</span>');
assert.strictEqual(Q.previewHtml('', esc), '');
""")


@node_only
def test_toolbar_placement():
    _node(r"""
const assert = require('assert');
const Q = require(process.argv[1]);
const view = { width: 800, height: 600 };
const size = { width: 300, height: 30 };
// Centred above the selection.
assert.deepStrictEqual(Q.placeToolbar({ top: 200, bottom: 220, left: 300, width: 100 }, size, view), { top: 162, left: 200 });
// No room above → below.
assert.strictEqual(Q.placeToolbar({ top: 10, bottom: 30, left: 300, width: 100 }, size, view).top, 38);
// Touch prefers below (the system menu sits above).
assert.strictEqual(Q.placeToolbar({ top: 200, bottom: 220, left: 300, width: 100 }, size, view, true).top, 228);
// Clamped to the viewport on both sides.
assert.strictEqual(Q.placeToolbar({ top: 200, bottom: 220, left: 0, width: 20 }, size, view).left, 6);
assert.strictEqual(Q.placeToolbar({ top: 200, bottom: 220, left: 780, width: 20 }, size, view).left, 494);
// A selection taller than the viewport still gets a visible toolbar.
const tall = Q.placeToolbar({ top: -50, bottom: 900, left: 300, width: 100 }, size, view);
assert.ok(tall.top >= 6 && tall.top + size.height <= view.height);
""")


def test_owner_script_loads_before_the_page():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert html.index("/js/chat/chat_quote_clips.js?v=") < html.index("/js/chat/chat_page.js?v=")
