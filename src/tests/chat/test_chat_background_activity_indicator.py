"""Background-work activity indicator: kind derivation, both history row
templates, the chat title slot and its CSS (CH-002112 report: a chat with
background work showed no spinner in the history panel or next to the title).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "src" / "web"
ACTIVITY_JS = WEB / "js" / "chat" / "chat_activity.js"
ATTENTION_JS = WEB / "js" / "chat" / "chat_attention.js"
HISTORY_VIEW_JS = WEB / "js" / "chat" / "chat_history_view.js"
CHAT_CSS = WEB / "css" / "chat_page.css"
CHAT_HTML = WEB / "chat_page.html"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def run_node(driver: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_background_kind_ranks_under_error_and_over_unread():
    driver = (
        "const A = require(%s);\n"
        "const base = { sessionId: '2', currentSessionId: '1', backgrounded: false };\n"
        "const k = (extra) => A.sessionHistoryAttentionKind(Object.assign({}, base, extra));\n"
        "const out = {};\n"
        "out.bg = k({ prefs: {}, sessionObj: { background: true }, queue: [] });\n"
        "out.bgOverUnread = k({ prefs: { hasUnread: true }, sessionObj: { background: true }, queue: [] });\n"
        "out.bgOverQueued = k({ prefs: {}, sessionObj: { background: true }, queue: [{ id: 'a' }] });\n"
        "out.errorWins = k({ prefs: { hasUnread: true, unreadIsError: true },\n"
        "  sessionObj: { background: true }, queue: [] });\n"
        "out.inputWins = k({ prefs: { awaitingInput: true }, sessionObj: { background: true }, queue: [] });\n"
        "out.noBg = k({ prefs: { hasUnread: true }, sessionObj: { background: false }, queue: [] });\n"
        "out.explicit = k({ prefs: {}, sessionObj: null, background: true, queue: [] });\n"
        "out.explicitOff = k({ prefs: {}, sessionObj: { background: true }, background: false, queue: [] });\n"
        "process.stdout.write(JSON.stringify(out));\n" % json.dumps(str(ACTIVITY_JS))
    )
    assert run_node(driver) == {
        "bg": "background",
        "bgOverUnread": "background",
        "bgOverQueued": "background",
        "errorWins": "error",
        "inputWins": "input",
        "noBg": "unread",
        "explicit": "background",
        "explicitOff": "",
    }


@node_only
def test_background_icon_html_and_running_still_wins():
    driver = (
        "require(%s);\n"
        "const C = CuttleChatAttention.create({ prefs: new Map(), doc: null,\n"
        "  escapeHtmlInline: (s) => String(s == null ? '' : s) });\n"
        "const out = { bg: C.historyAttentionIconHTML('background'),\n"
        "  running: C.historyAttentionIconHTML('running'),\n"
        "  none: C.historyAttentionIconHTML('') };\n"
        "process.stdout.write(JSON.stringify(out));\n" % json.dumps(str(ATTENTION_JS))
    )
    out = run_node(driver)
    assert "history-background-icon" in out["bg"]
    assert "Working in the background" in out["bg"]
    assert "history-running-icon" in out["running"] and "history-background-icon" not in out["running"]
    assert out["none"] == ""


@node_only
def test_both_history_row_templates_paint_the_background_spinner():
    """The tree/parent row and the plain row: the regression was that neither
    ever showed background state as a spinner."""
    driver = (
        "require(%s);\nrequire(%s);\n"
        "const att = CuttleChatAttention.create({ prefs: new Map(), doc: null,\n"
        "  escapeHtmlInline: (s) => String(s == null ? '' : s) });\n"
        "const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');\n"
        "const mk = (kind, running) => CuttleChatHistoryView.createRenderers({\n"
        "  escapeHtml: esc, escapeHtmlInline: esc,\n"
        "  currentSessionId: () => '1',\n"
        "  sessionIdsEqual: (a, b) => String(a) === String(b),\n"
        "  sessionShowsHistorySpinner: () => running,\n"
        "  sessionHistoryAttentionKind: () => kind,\n"
        "  resolveSessionProjectInfo: () => null, projectChipHtml: () => '',\n"
        "  historyBranchFor: () => '', chatTitleParts: (t) => ({ chipsHtml: '', titleHtml: esc(t) }),\n"
        "  historyAttentionIconHTML: att.historyAttentionIconHTML,\n"
        "  openPaneEntriesForSession: () => [], historyOpenPaneBadgeHTML: () => '',\n"
        "  historySubagentToggleTitle: () => '', formatHistoryLastMessageAge: () => '1m',\n"
        "  resolveHistoryLastMessageMs: () => 0, historyRunningIconHTML: att.historyRunningIconHTML,\n"
        "  formatChatDisplayId: (id) => 'CH-' + id,\n"
        "});\n"
        "const sess = { id: '2112', session_name: 'Background chat', title: 'Background chat',\n"
        "  background: true, message_count: 3 };\n"
        "const bg = mk('background', false), run = mk('running', true);\n"
        "const out = {\n"
        "  treeBg: bg.createAuthHistoryItemHTML(sess, { subagentChildCount: 2 }),\n"
        "  plainBg: bg.createHistoryItemHTML(sess),\n"
        "  treeRun: run.createAuthHistoryItemHTML(sess, { subagentChildCount: 2 }),\n"
        "  plainRun: run.createHistoryItemHTML(sess),\n"
        "};\n"
        "process.stdout.write(JSON.stringify(out));\n"
        % (json.dumps(str(ATTENTION_JS)), json.dumps(str(HISTORY_VIEW_JS)))
    )
    out = run_node(driver)
    for key in ("treeBg", "plainBg"):
        assert "history-background-icon" in out[key], key
        assert "has-background" in out[key], key
        assert "history-running-icon" not in out[key], key
    # A live run still owns the one status slot.
    for key in ("treeRun", "plainRun"):
        assert "history-running-icon" in out[key], key
        assert "history-background-icon" not in out[key], key


def test_chat_title_has_a_background_slot_and_quieter_spinner_css():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert 'id="chatSessionBackgroundIcon"' in html
    assert "Working in the background" in html
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert ".history-background-icon" in css and ".chat-session-background-icon" in css
    block = css.split(".history-background-icon,", 1)[1].split("html.cuttle-bg-paused", 1)[0]
    # A real wheel: a 2px full-strength accent arc with one gap, hollow inside.
    assert "border: 2px solid var(--accent-color, #667eea);" in block
    assert "border-right-color: transparent;" in block
    assert "dashed" not in block and "dotted" not in block
    assert "history-background-spin" in block
    spin = float(block.split("history-background-spin", 1)[1].split("s ", 1)[0].strip())
    run_block = css.split("\n.history-running-icon {", 1)[1].split("}", 1)[0]
    run_spin = float(run_block.split("history-running-spin", 1)[1].split("s ", 1)[0].strip())
    assert spin > run_spin
    # Reduced motion and the backgrounded shell stop it like the other dots.
    tail = css.split(".history-background-icon,", 1)[1]
    assert "prefers-reduced-motion" in tail
    # Reduced motion parks it; it must not be dimmed or restyled to stay quiet.
    reduced = tail.split("prefers-reduced-motion", 1)[1].split("}", 2)[0]
    assert "animation: none !important;" in reduced
    assert "opacity" not in reduced and "border" not in reduced
    assert "html.cuttle-bg-paused .history-background-icon" in css


def test_title_projection_ranks_background_under_error_over_queued():
    js = (WEB / "js" / "chat" / "chat_attention.js").read_text(encoding="utf-8")
    body = js.split("function updateSessionTitleUnreadDot", 1)[1]
    assert "chatSessionBackgroundIcon" in body
    # Never beside the live-run spinner, and it suppresses unread/queued/paused.
    assert "!sessionIsRunning(current)" in body
    assert "const showUnread = unreadActive && !showBackground;" in body
    assert body.count("&& !showBackground") >= 2
