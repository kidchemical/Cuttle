"""Action-card effects controller frontend domain.

Characterization of src/web/js/chat/chat_action_cards.js (plan C2): the page
composes one `CuttleChatActionCards.mountCards(root, host)` instance per
chat root and keeps every send lane, panel, composer, and transport
surface; the controller owns card activation/submission, dismissal,
lock/progress DOM effects, watch/restart loops, choice storage, and
linked-restart recovery through explicit host capabilities.

Covers the module contract under node (parse, namespace, load order)
plus the factory surface that the isolated browser suite drives with
real DOM and real fetch plumbing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_action_cards.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@node_only
def test_chat_action_cards_module_parses():
    proc = subprocess.run(["node", "--check", str(MOD_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr


@node_only
def test_chat_action_cards_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(
        encoding="utf-8")
    tag = '<script src="/js/chat/chat_action_cards.js?v='
    assert tag in html, "chat_action_cards.js must load via versioned script tag"
    assert html.index("chat_activity.js") < html.index(
        "chat_action_cards.js") < html.index("chat_page.js?v="), \
        "load order: pure owners before the controller before the page"


@node_only
def test_chat_action_cards_factory_validates_and_composes():
    probe = (
        "const api = require(%s);\n"
        "const assert = require('assert');\n"
        "assert.strictEqual(typeof api.mountCards, 'function');\n"
        "const root = { querySelectorAll: () => [], contains: () => true,\n"
        "  ownerDocument: { querySelectorAll: () => [] } };\n"
        "const store = { _m: {},\n"
        "  getItem(k) { return this._m[k] === undefined ? null : this._m[k]; },\n"
        "  setItem(k, v) { this._m[k] = String(v); },\n"
        "  removeItem(k) { delete this._m[k]; } };\n"
        "const host = { request: () => Promise.reject(new Error('x')),\n"
        "  sendAnswerText: () => {}, resumeWithStatus: () => Promise.resolve(),\n"
        "  sendControlCommand: () => Promise.resolve(),\n"
        "  context: () => ({ sessionId: '1' }), storage: store,\n"
        "  notify: () => {}, syncMessages: () => Promise.resolve(),\n"
        "  publishRestartEvent: () => {},\n"
        "  setHistoryAwaiting: () => {}, syncHistoryAwaiting: () => {},\n"
        "  syncComposerStop: () => {}, paintAssistantMessage: () => {},\n"
        "  escapeHtml: (s) => String(s) };\n"
        "assert.throws(() => api.mountCards(null, host), /chat root/);\n"
        "assert.throws(() => api.mountCards({}, host), /chat root/);\n"
        "assert.throws(() => api.mountCards(root, {}), /host capabilities/);\n"
        "assert.throws(() => api.mountCards(root, Object.assign({}, host,\n"
        "  { storage: { getItem() {}, setItem() {} } })), /storage/);\n"
        "const ctl = api.mountCards(root, host);\n"
        "for (const m of ['mount', 'dismissOpenActionForms',\n"
        "  'sendDismissNotice', 'handleExternalRestart', 'runningWatchJobIds',\n"
        "  'awaitingSessionId', 'disposeCard', 'destroy']) {\n"
        "  assert.strictEqual(typeof ctl[m], 'function', m);\n"
        "}\n"
        "assert.deepStrictEqual(ctl.runningWatchJobIds(), []);\n"
        "ctl.disposeCard(null);\n"
        "ctl.mount(null);\n"
        "ctl.destroy();\n"
        "ctl.destroy();\n"
        "console.log('factory interface ok');\n" % (
            __import__("json").dumps(str(MOD_JS)))
    )
    proc = subprocess.run(["node", "-e", probe],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert "factory interface ok" in proc.stdout
