"""Messages/History frontend domain.

Behavioral characterization of src/web/js/chat/chat_messages.js under node
(Phase 3 Slice 8). Covers the pure message-record and history-window
decisions moved out of chat_page.js: server-record option building
(metadata shapes, timestamp/attachment delegation, report URL,
project/supervised fields), latest-model recovery, project
annotation, visible counting, history windowing/pagination meta, and
the transcript-end predicate.

The page keeps: transcript DOM paint, message sync/poll machinery,
persistence, session restore + ordering, search/history-panel UI,
prompt history, streaming and live-status orchestration, and the
DOM-coupled predicates (thisTurnHasAssistantReply,
transcriptEndsWithGenerationStop, uiAlreadyHasMessage). Cross-domain
formatting stays in its owner (attachments via injected callback,
project resolution via injected callback).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_messages.js"
MOD_MD = REPO_ROOT / "src" / "web" / "js" / "chat/chat_markdown.js"
MOD_USAGE = REPO_ROOT / "src" / "web" / "js" / "chat/chat_usage_live.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
const deps = {
  parseSessionTimestamp: (v) => (typeof v === 'number' ? v : Date.parse(String(v || '')) || 0),
  normalizeAttachmentList: (l) => (Array.isArray(l) ? l : []).map((a) => ({ filename: String((a && a.filename) || '') })),
  normalizeUsagePayload: (u) => (u && typeof u === 'object' ? u : null),
  projectFromMessageOpts: (opts) => (opts && opts.project_id != null ? { id: opts.project_id } : null),
  projects: [],
  currentProject: null,
};
// record options: metadata shapes
out.stringMeta = A.authMessageOptsFromServer(
  { id: 7, content: 'hi', role: 'user', timestamp: 1700000000000,
    metadata: JSON.stringify({ kind: 'note', project_id: 3 }) }, deps);
out.objectMeta = A.authMessageOptsFromServer(
  { id: 8, content: 'x', role: 'assistant', timestamp: 1700000000000,
    metadata: { slash_command: { chips: [] }, cursor_run: { requested_model: 'm' } } }, deps);
out.badMeta = A.authMessageOptsFromServer(
  { id: 9, content: 'x', role: 'user', timestamp: 1700000000000, metadata: '{oops' }, deps);
out.noMeta = A.authMessageOptsFromServer(
  { id: 10, content: 'x', role: 'user', timestamp: 1700000000000 }, deps);
// report URL precedence: direct > meta > query_id link > null
out.reportDirect = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, report_url: 'r', metadata: { report_url: 'm', query_id: 'q' } }, deps).report_url;
out.reportMeta = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, metadata: { report_url: 'm', query_id: 'q' } }, deps).report_url;
out.reportQuery = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, query_id: 'q', metadata: {} }, deps).report_url;
out.reportNone = A.authMessageOptsFromServer({ id: 1, timestamp: 1 }, deps).report_url;
// attachments + usage delegation
out.atts = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, attachments: [{ filename: 'a.png' }] }, deps).attachments;
out.noAtts = A.authMessageOptsFromServer({ id: 1, timestamp: 1 }, deps).attachments || null;
out.usage = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, metadata: { usage: { total: 5 } } }, deps).usage;
// project fields: direct > meta > _project
out.projDirect = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, project_id: 9, metadata: { project_id: 4 }, _project: { id: 2 } }, deps).project_id;
out.projMeta = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, metadata: { project_id: 4 }, _project: { id: 2 } }, deps).project_id;
out.projNest = A.authMessageOptsFromServer(
  { id: 1, timestamp: 1, _project: { id: 2 } }, deps).project_id;
// latest model recovery
const msgs = [
  { role: 'user', content: 'a' },
  { role: 'assistant', content: 'b', metadata: { cursor_run: { requested_model: 'old' } } },
  { role: 'assistant', content: 'c', metadata: JSON.stringify({ cursor_run: { requested_model: 'new' } }) },
  { role: 'assistant', content: 'd', metadata: '{bad' },
];
out.model = A.preferredModelFromMessages(msgs);
out.modelNone = A.preferredModelFromMessages([{ role: 'user', content: 'a' }]);
out.modelEmpty = A.preferredModelFromMessages([]);
out.modelNonArray = A.preferredModelFromMessages(null);
// project record + annotation (backward fill)
out.recNested = A.projectFromMessageRecord({ _project: { id: 1 } }, deps);
out.recOpts = A.projectFromMessageRecord({ project_id: 5 }, deps);
out.recNone = A.projectFromMessageRecord({ content: 'x' }, deps);
out.recNull = A.projectFromMessageRecord(null, deps);
const ann = [{ content: 'u1' }, { content: 'a1', project_id: 8 }, { content: 'u2' }];
A.annotateMessageProjects(ann, deps);
out.annIds = ann.map((m) => (m._project && m._project.id) || null);
out.annNoop = A.annotateMessageProjects(null, deps);
// visible counting skips system rows
out.count = A.countVisibleChatMessages(
  [{ role: 'user' }, { role: 'assistant' }, { role: 'system' }, null]);
out.countNonArray = A.countVisibleChatMessages(null);
// windowing: server-paged passthrough
const big = [];
for (let i = 1; i <= 25; i++) big.push({ id: i, role: i % 2 ? 'user' : 'assistant' });
out.paged = A.windowHistoryMessages(
  { messages: big.slice(20), hasMore: true, olderVisibleCount: 19, pageSize: 10 });
out.small = A.windowHistoryMessages({ messages: big.slice(0, 5), pageSize: 10 });
out.smallEmpty = A.windowHistoryMessages({ messages: [], pageSize: 10 });
out.large = A.windowHistoryMessages({ messages: big, pageSize: 10 });
out.exact = A.windowHistoryMessages({ messages: big.slice(0, 10), pageSize: 10 });
// page meta standalone
out.metaFull = A.historyPageMeta(
  { hasMore: false, olderVisibleCount: 3, messages: [{ id: 42 }] });
out.metaKeep = A.historyPageMeta({ messages: [] });
// transcript-end predicate
out.endsAssistant = A.transcriptEndsWithAssistant([{ role: 'user' }, { role: 'assistant' }]);
out.endsUser = A.transcriptEndsWithAssistant([{ role: 'assistant' }, { role: 'user' }]);
out.endsEmpty = A.transcriptEndsWithAssistant([]);
out.endsNonArray = A.transcriptEndsWithAssistant(null);
process.stdout.write(JSON.stringify(out));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_record_options_metadata_shapes():
    res = _run()
    assert res["stringMeta"]["message_id"] == 7
    assert res["stringMeta"]["kind"] == "note"
    assert res["stringMeta"]["project_id"] == 3
    assert res["objectMeta"]["slash_command"] == {"chips": []}
    assert res["objectMeta"]["cursor_run"] == {"requested_model": "m"}
    assert res["badMeta"].get("kind") is None
    assert res["noMeta"].get("project_id") is None


@node_only
def test_record_options_report_attachments_usage():
    res = _run()
    assert res["reportDirect"] == "r"
    assert res["reportMeta"] == "m"
    assert res["reportQuery"] == "/query_log.html?id=q"
    assert res["reportNone"] is None
    assert res["atts"] == [{"filename": "a.png"}]
    assert res["noAtts"] is None
    assert res["usage"] == {"total": 5}
    assert res["projDirect"] == 9
    assert res["projMeta"] == 4
    assert res["projNest"] == 2


@node_only
def test_model_recovery_project_annotation_counting():
    res = _run()
    assert res["model"] == "new"
    assert res["modelNone"] is None
    assert res["modelEmpty"] is None
    assert res["modelNonArray"] is None
    assert res["recNested"] == {"id": 1}
    assert res["recOpts"] == {"id": 5}
    assert res["recNone"] is None
    assert res["recNull"] is None
    assert res["annIds"] == [8, 8, None]
    assert "annNoop" not in res
    assert res["count"] == 2
    assert res["countNonArray"] == 0


@node_only
def test_history_windowing_and_meta():
    res = _run()
    assert [m["id"] for m in res["paged"]["paint"]] == [21, 22, 23, 24, 25]
    assert res["paged"]["buffer"] == []
    assert res["paged"]["hasMore"] is True
    assert res["paged"]["olderVisibleCount"] == 19
    assert res["paged"]["oldestId"] == 21
    assert [m["id"] for m in res["small"]["paint"]] == [1, 2, 3, 4, 5]
    assert res["small"]["buffer"] == []
    assert res["small"]["hasMore"] is False
    assert res["small"]["olderVisibleCount"] == 0
    assert res["small"]["oldestId"] == 1
    assert res["smallEmpty"]["paint"] == []
    assert res["smallEmpty"]["buffer"] == []
    # empty dump keeps the current oldest id (key absent, not nulled)
    assert "oldestId" not in res["smallEmpty"]
    assert [m["id"] for m in res["large"]["paint"]] == list(range(16, 26))
    assert [m["id"] for m in res["large"]["buffer"]] == list(range(1, 16))
    assert res["large"]["hasMore"] is True
    assert res["large"]["olderVisibleCount"] == 15
    assert res["large"]["oldestId"] == 16
    assert [m["id"] for m in res["exact"]["paint"]] == list(range(1, 11))
    assert res["exact"]["buffer"] == []
    assert res["exact"]["hasMore"] is False
    assert res["exact"]["oldestId"] == 1
    assert res["metaFull"] == {"hasMore": False, "olderVisibleCount": 3, "oldestId": 42}
    assert res["metaKeep"] == {}


@node_only
def test_transcript_end_predicate():
    res = _run()
    assert res["endsAssistant"] is True
    assert res["endsUser"] is False
    assert res["endsEmpty"] is False
    assert res["endsNonArray"] is False


@node_only
def test_chat_messages_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr


CHAT_ATTACHMENTS_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_attachments.js"
CHAT_SLASH_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_slash.js"

# Renders through the REAL moved formatter (chat_messages.js) with the
# REAL page leaf bodies that the formatter consumes (extracted by exact
# markers, never copied): escapeHtmlInline, parseButtonClickFromContent
# (+ real button labels), formatFormReplyHtml (+ real icon),
# buildMessageAttachmentsHtml (+ real mediaDownloadUrl) and the REAL
# attachments/slash modules. Only the markdown renderer and the slash
# chip HTML leaves are thin labeled stubs — their own units own their
# internals; the assertions pin the formatter's dispatch structure,
# composition order, and the escaping it performs itself.
FORMAT_HARNESS = """
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const span = (s, e) => SRC.slice(SRC.indexOf(s), SRC.indexOf(e, SRC.indexOf(s)));
const CuttleChatAttachments = require(process.env.CHAT_ATTACHMENTS_JS);
const CuttleChatSlash = require(process.env.CHAT_SLASH_JS);
const A = require(process.env.MOD_JS);
const normalizeAttachmentList = CuttleChatAttachments.normalizeAttachmentList;
eval(span('    function escapeHtmlInline(s) {',
          '    function windowsPathToFileUrl(path) {'));
eval(span('    const CUTTLE_KNOWN_BUTTON_LABELS = {',
          '    function parseButtonClickFromContent(content) {')
  .replace('const CUTTLE_KNOWN_BUTTON_LABELS = {',
           'globalThis.CUTTLE_KNOWN_BUTTON_LABELS = {'));
eval(span('    function parseButtonClickFromContent(content) {',
          '    function parseButtonClickFromMessage(msg) {'));
eval(span('    function formatFormReplyHtml(content) {',
          '    function formatUserMessageForDisplay(content, opts = {}) {'));
eval(span('    function mediaDownloadUrl(src) {',
          '    function mediaDownloadFilename(src, title) {'));
eval(span('    function buildMessageAttachmentsHtml(attachments) {',
          '    function formatMessageWithAttachments(message, attachments) {'));
for (const iconName of ['FORM_REPLY_ICON_PICK', 'FORM_REPLY_ICON_ANSWERS']) {
  const at = SRC.indexOf('    const ' + iconName + ' =');
  const line = SRC.slice(at);
  const stmt = line.slice(0, line.indexOf('\\n'))
    .replace('    const ' + iconName + ' =', 'globalThis.' + iconName + ' =')
    .replace(/;\s*$/, '');
  eval(stmt);
}
const formatMessage = (t) => '<md>' + String(t ?? '') + '</md>';
const collapseCursorSlashChips = (chips) => (Array.isArray(chips) ? chips.slice() : []);
const slashCommandChipHistoryHtml = (label, meta, cat) =>
  '<chip:' + String(label) + '|' + String(meta) + '>';
const parseStoredSlashCommandMessage = (t) =>
  CuttleChatSlash.parseStoredSlashCommandMessage(t);
const deps = { normalizeAttachmentList,
  stripAttachedNote: CuttleChatAttachments.stripAttachedNote,
  inferAttachmentsFromContent: (c) => CuttleChatAttachments.inferAttachmentsFromContent({ content: c, sessionId: '' }),
  buildMessageAttachmentsHtml, parseButtonClickFromContent, formatFormReplyHtml,
  formatMessage, parseStoredSlashCommandMessage,
  collapseCursorSlashChips, slashCommandChipHistoryHtml, escapeHtmlInline };
const F = (content, opts) => A.formatUserMessageForDisplay(content, opts, deps);
const out = {};
out.plain = F('hello world', {});
out.selected = F('Selected:  Foo <bar>', {});
out.buttonKnown = F('[button:launch-local-llm-yes]', {});
out.buttonGeneric = F('[button:approve-this]', {});
out.buttonXss = F('[button:<img src=x>]', {});
out.formSel = F('[form-selection] Pick A (a), Pick B (b)', {});
out.formAns = F('[form-answers]\\n- Color: red\\n- Size: (no answer)', {});
out.slashBody = F('/cursor fix it', {});
out.slashBare = F('/cursor', {});
out.slashSuppressed = F('/cursor fix it', { suppressInlineSlashChips: true });
out.attachOnly = F('(see attached files)',
  { attachments: [{ filename: 'a.png', url: '/output/uploads/s/a.png' }] });
out.textAttach = F('see this', { attachments: [{ filename: 'a.png', url: '/output/uploads/s/a.png' }] });
out.empty = F('', {});
out.nullContent = F(null, {});
out.inferred = F('look [Attached: old.png]', {});
process.stdout.write(JSON.stringify(out));
"""


def _run_format():
    import os
    proc = subprocess.run(
        ["node", "-e", FORMAT_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "CHAT_PAGE_JS": str(CHAT_PAGE_JS),
             "CHAT_ATTACHMENTS_JS": str(CHAT_ATTACHMENTS_JS),
             "CHAT_SLASH_JS": str(CHAT_SLASH_JS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_user_bubble_selection_button_form_branches():
    res = _run_format()
    assert res["plain"] == "<md>hello world</md>"
    assert res["selected"] == (
        '<div class="cuttle-button-selection">Selected: '
        "<strong>Foo &lt;bar&gt;</strong></div>")
    assert res["buttonKnown"] == (
        '<div class="cuttle-button-selection">Selected: '
        "<strong>Yes, launch llama.cpp</strong></div>")
    assert "approve this" in res["buttonGeneric"]
    # angle brackets in a button id are escaped, not emitted as markup
    assert "&lt;img src=x&gt;" in res["buttonXss"]
    assert "<img src=x>" not in res["buttonXss"]
    assert 'class="cuttle-form-reply"' in res["formSel"]
    assert "Pick A, Pick B" in res["formSel"]
    assert "(a)" not in res["formSel"]
    assert "Color" in res["formAns"] and "red" in res["formAns"]
    assert "is-empty" in res["formAns"]


@node_only
def test_user_bubble_slash_chip_branches():
    res = _run_format()
    assert 'class="user-message-with-slash"' in res["slashBody"]
    assert "<chip:Cursor Agent|/cursor>" in res["slashBody"]
    assert "<md>fix it</md>" in res["slashBody"]
    assert 'class="user-message-with-slash"' in res["slashBare"]
    assert "user-slash-body" not in res["slashBare"]
    assert "slash-chips-inline" not in res["slashSuppressed"]
    assert "<md>fix it</md>" in res["slashSuppressed"]


@node_only
def test_user_bubble_attachment_composition_and_fallbacks():
    res = _run_format()
    assert "msg-attach-thumb" in res["attachOnly"]
    assert "(see attached files)" not in res["attachOnly"]
    assert 'class="user-message-with-attachments"' in res["textAttach"]
    assert "<md>see this</md>" in res["textAttach"]
    assert "msg-attach-thumb" in res["textAttach"]
    assert res["empty"] == "<md></md>"
    # null content falls through to the markdown leaf like empty text
    assert res["nullContent"] == res["empty"]
    # history text without metadata infers the attachment ref
    assert "msg-attach-thumb" in res["inferred"]
    assert "old.png" in res["inferred"]


STRUCTURED_HARNESS = """
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const span = (s, e) => SRC.slice(SRC.indexOf(s), SRC.indexOf(e, SRC.indexOf(s)));
const A = require(process.env.MOD_JS);
eval(span('    function escapeHtmlInline(s) {',
          '    function windowsPathToFileUrl(path) {'));
eval(span('    function clamp(n, min, max) {',
          '    function safeJsonParse(s) {'));
const deps = { escapeHtmlInline, clamp,
  renderMdLinkChip: (label, url) => '<a data-url="' + url + '">' + label + '</a>',
  renderCuttlePricingHtml: (raw) => '<price:' + String(raw).slice(0, 12) + '>',
  mediaKindFromUrl: (u) => (/\\.(mp4|webm)$/i.test(String(u).split('?')[0]) ? 'video' : 'image'),
  buildMediaThumbHtml: (src, o) => '<thumb src="' + src + '" title="' + ((o && o.title) || '') + '">' };
const E = (text) => A.extractHeadStructuredBlocks(text, deps);
const T = (text) => A.extractTailStructuredBlocks(text, deps);
const out = {};
out.think = E('a <think>reason <b>here</b></think> b');
out.thinkFenced = E('```\\n<think>x</think>\\n```');
out.thinkInlineCode = E('`<think>x</think>` and <think>real</think>');
out.thinkUnclosed = E('a <think>oops b');
out.thinkXss = E('<think><script>alert(1)</script></think>');
out.tool = E('<tool_output>ls <b>out</b></tool_output>');
const tailIn = '<cuttle_trace>t <i>x</i></cuttle_trace> <progress id="p\\"1" label="L<o" value="42"/> <terminal id="t1" title="T\\"tle" interactive="true">echo hi</terminal>';
out.tail = T(tailIn);
out.meters = T('<cuttle_meters>{"rows": [{"label": "M<x", "pct": 33}]}</cuttle_meters>');
out.metersBad = T('<cuttle_meters>not json</cuttle_meters>');
out.metersDisabled = T('<cuttle_meters>{"rows": [{"label": "D", "pct": 90, "disabled": true, "status": "off"}]}</cuttle_meters>');
out.metersReset = T('<cuttle_meters>{"rows": [{"label": "5-hour", "pct": 0, "tooltip": "Resets", "tooltip_at": 1893456000}, {"label": "Weekly", "pct": 50}]}</cuttle_meters>');
out.metersNoReset = T('<cuttle_meters>{"rows": [{"label": "Credits", "pct": 0, "disabled": true, "status": "None"}]}</cuttle_meters>');
out.unblockFmtHour = A.formatUnblockCountdown(3*3600*1000 + 13*60*1000);
out.unblockFmtMin = A.formatUnblockCountdown(42*60*1000 + 10*1000);
out.unblockFmtPast = A.formatUnblockCountdown(-1000);
out.pricing = T('<cuttle_pricing>{"model": "m1"}</cuttle_pricing>');
out.mediaAudio = T('<media type="audio" src="s.mp3" title="T\\"t"></media>');
out.mediaImg = T('<media src="pic.png" title="P"></media>');
out.mediaNoSrc = T('<media type="image" title="P"></media>');
out.vegaTag = T('<vega>{"mark": "point"}</vega>');
out.vegaFence = T('```vega\\n{"mark": "x"}\\n```');
out.vegaEmpty = A.buildVegaWrapHtml('   ', deps);
out.vegaXss = A.buildVegaWrapHtml('{"a": "</div><script>}', deps);
out.empty = E('');
out.nullText = T(null);
const combined = {};
for (const part of [out.think.blocks, out.tool.blocks, out.tail.blocks]) {
  for (const k of Object.keys(part)) combined[k] = (combined[k] || []).concat(part[k]);
}
out.restored = A.restoreStructuredBlocks(
  out.think.text + ' ' + out.tool.text + ' ' + out.tail.text, combined);
out.restoreUnknown = A.restoreStructuredBlocks('keep {{CUTTLE_NOPE_0}} here', {});
out.restoreEmpty = A.restoreStructuredBlocks('plain', { think: [], tool: [] });
out.copyClean = A.cleanCodeCopyText
  ? A.cleanCodeCopyText('a\\u200Bb\\u00A0c\\r\\n\\n')
  : 'module-has-no-cleanCodeCopyText';
// Post-paint activation lives in chat_activate.js (test_chat_activate.py
// executes it); planning coverage here stops at the wrap HTML above.
process.stdout.write(JSON.stringify(out));
"""


def _run_structured():
    import os
    proc = subprocess.run(
        ["node", "-e", STRUCTURED_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "CHAT_PAGE_JS": str(CHAT_PAGE_JS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_structured_block_planning_extracts_and_escapes():
    res = _run_structured()
    think = res["think"]
    assert think["blocks"]["think"] and len(think["blocks"]["think"]) == 1
    assert "{{CUTTLE_THINK_0}}" in think["text"]
    assert "reason <b>here</b>" not in think["blocks"]["think"][0]
    assert "reason &lt;b&gt;here&lt;/b&gt;" in think["blocks"]["think"][0]
    # tags inside fenced/inline code are protected, real ones still extract
    assert res["thinkFenced"]["blocks"]["think"] == []
    assert "<think>" in res["thinkFenced"]["text"]
    assert len(res["thinkInlineCode"]["blocks"]["think"]) == 1
    assert res["thinkUnclosed"]["blocks"]["think"] == []
    assert "&lt;script&gt;" in res["thinkXss"]["blocks"]["think"][0]
    assert "<script>" not in res["thinkXss"]["blocks"]["think"][0]
    tool = res["tool"]
    assert "{{CUTTLE_TOOL_0}}" in tool["text"]
    assert "&lt;b&gt;out&lt;/b&gt;" in tool["blocks"]["tool"][0]


@node_only
def test_structured_tail_blocks_attributes_and_fallbacks():
    res = _run_structured()
    tail = res["tail"]
    assert "{{CUTTLE_TRACE_0}}" in tail["text"]
    assert "&lt;i&gt;" in tail["blocks"]["trace"][0]
    assert "{{CUTTLE_PROGRESS_0}}" in tail["text"]
    # quote-bearing attrs truncate at the inner quote (inherited regex behavior)
    assert 'data-progress-id="p"' in tail["blocks"]["progress"][0]
    assert "L&lt;o" in tail["blocks"]["progress"][0]
    assert "{{CUTTLE_TERM_0}}" in tail["text"]
    assert 'data-interactive="true"' in tail["blocks"]["terminal"][0]
    assert 'terminal-title">T</div>' in tail["blocks"]["terminal"][0]
    assert "M&lt;x" in res["meters"]["blocks"]["meters"][0]
    assert res["meters"]["blocks"]["meters"][0].count("cuttle-meter-row") >= 1
    assert res["metersBad"]["blocks"]["meters"][0].count("cuttle-meter-row") == 0
    assert "cuttle-meter-row--disabled" in res["metersDisabled"]["blocks"]["meters"][0]
    reset_html = res["metersReset"]["blocks"]["meters"][0]
    assert "cuttle-meter-unblock" in reset_html
    assert 'data-unblock-at="1893456000"' in reset_html
    assert "Unblocked in" in reset_html
    assert "5-hour" in reset_html
    assert "cuttle-meter-unblock" not in res["metersNoReset"]["blocks"]["meters"][0]
    assert res["unblockFmtHour"] == "in 3h 13m"
    assert res["unblockFmtMin"] == "in 42m 10s"
    assert "refresh /usage" in res["unblockFmtPast"]
    assert res["pricing"]["blocks"]["pricing"] == ['<price:{"model": "m>']
    assert "msg-audio" not in res["mediaAudio"]["blocks"]["media"][0]
    assert "<audio" in res["mediaAudio"]["blocks"]["media"][0]
    # quote-bearing attrs truncate at the inner quote (inherited regex behavior)
    assert 'ui-title">T</div>' in res["mediaAudio"]["blocks"]["media"][0]
    assert "<thumb" in res["mediaImg"]["blocks"]["media"][0]
    assert res["mediaNoSrc"]["text"] == ""
    assert res["mediaNoSrc"]["blocks"]["media"] == []


@node_only
def test_vega_wrap_planning():
    res = _run_structured()
    assert res["vegaEmpty"] == (
        '<div class="vega-wrap vega-wrap--error">Empty Vega chart</div>')
    assert 'data-vega-spec="{&quot;a&quot;: &quot;&lt;/div&gt;&lt;script&gt;}' in res["vegaXss"]
    assert "</div><script>" not in res["vegaXss"]
    assert "{{CUTTLE_VEGA_0}}" in res["vegaTag"]["text"]
    assert "{{CUTTLE_VEGA_0}}" in res["vegaFence"]["text"]


@node_only
def test_structured_restore_and_copy_text():
    res = _run_structured()
    assert "{{CUTTLE_" not in res["restored"]
    assert "thinking-block" in res["restored"]
    assert "tool-block" in res["restored"]
    assert res["restoreUnknown"] == "keep {{CUTTLE_NOPE_0}} here"
    assert res["restoreEmpty"] == "plain"
    assert res["empty"] == {"text": "", "blocks": {"think": [], "tool": []}}
    assert res["nullText"] == {"text": "", "blocks": {
        "trace": [], "progress": [], "meters": [], "pricing": [],
        "terminal": [], "media": [], "vega": []}}
    # zero-width chars drop (no space), exotic spaces flatten
    assert res["copyClean"] == "ab c"


CODELINK_HARNESS = """
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const span = (s, e) => { const h = SRC.indexOf(s); const t = SRC.indexOf(e, h);
  if (h < 0 || t < 0) throw new Error('bad marker: ' + s.slice(0, 50)); return SRC.slice(h, t); };
const A = require(process.env.MOD_JS);
eval(span('    function escapeHtmlInline(s) {', '    function windowsPathToFileUrl(path) {'));
eval(span('    function windowsPathToFileUrl(path) {', '    function normalizeMdHref(url) {'));
eval(span('    function normalizeMdHref(url) {', '    function isSafeMdHref(url) {'));
eval(span('    function isSafeMdHref(url) {', '    function mdLinkChipLabel(label, url) {'));
eval(span('    function mdLinkChipLabel(label, url) {', '    function renderMdLinkChip(label, url) {'));
const renderMdLinkChip = (label, url) => '<mdchip label="' + label + '" url="' + url + '">';
const deps = { escapeHtmlInline, renderMdLinkChip, isSafeMdHref };
const E = (text) => A.extractCodeLinkBlocks(text, deps);
const out = {};
out.code = E('before\\n```python\\nprint("<x>")\\n```\\nafter');
out.codeNoLang = E('```\\nplain\\n```');
out.codeBadLang = E('```<bad lang!>\\nx\\n```');
out.codeUnclosed = E('text ```js\\nopen');
out.codeTrailNl = E('```\\ncode\\n```');
out.link = E('See [docs](https://example.com/a).');
out.linkUnsafe = E('[x](javascript:alert(1))');
out.bare = E('go https://example.com/b, now');
out.bareUnsafe = E('go javascript:alert(1) now');
out.bareFtp = E('get ftp://h/f now');
out.linkInCode = E('```\\n[not a link](https://example.com/z)\\n```\\n[real](https://example.com/r)');
out.fakePlaceholder = E('keep {{CUTTLE_CODE_9}} and {{CUTTLE_LINK_9}}');
out.empty = E('');
out.nullText = E(null);
const combinedBlocks = {};
for (const part of [out.code.blocks, out.link.blocks, out.bare.blocks]) {
  for (const k of Object.keys(part)) combinedBlocks[k] = (combinedBlocks[k] || []).concat(part[k]);
}
out.restored = A.restoreStructuredBlocks(
  out.code.text + ' ' + out.link.text + ' ' + out.bare.text, combinedBlocks);
process.stdout.write(JSON.stringify(out));
"""


def _run_codelink():
    import os
    proc = subprocess.run(
        ["node", "-e", CODELINK_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "CHAT_PAGE_JS": str(CHAT_PAGE_JS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_code_fence_planning_escapes_and_protects():
    res = _run_codelink()
    code = res["code"]
    assert "{{CUTTLE_CODE_0}}" in code["text"]
    assert "before" in code["text"] and "after" in code["text"]
    assert 'class="language-python"' in code["blocks"]["code"][0]
    assert "print(&quot;&lt;x&gt;&quot;)" in code["blocks"]["code"][0]
    assert "<code>plain</code>" in res["codeNoLang"]["blocks"]["code"][0]
    assert 'class="language-' not in res["codeBadLang"]["blocks"]["code"][0]
    assert res["codeUnclosed"]["blocks"]["code"] == []
    assert "```js" in res["codeUnclosed"]["text"]
    assert res["codeTrailNl"]["blocks"]["code"][0].endswith("<code>code</code></pre>")


@node_only
def test_link_planning_chips_and_safety():
    res = _run_codelink()
    assert "{{CUTTLE_LINK_0}}" in res["link"]["text"]
    assert 'url="https://example.com/a"' in res["link"]["blocks"]["link"][0]
    assert 'label="x"' in res["linkUnsafe"]["blocks"]["link"][0]
    assert "{{CUTTLE_LINK_0}}" in res["bare"]["text"]
    assert res["bare"]["text"].endswith(", now")
    assert "javascript:" in res["bareUnsafe"]["text"]
    assert "{{CUTTLE_LINK_" not in res["bareUnsafe"]["text"]
    assert "{{CUTTLE_LINK_" not in res["bareFtp"]["text"]
    assert len(res["linkInCode"]["blocks"]["code"]) == 1
    assert len(res["linkInCode"]["blocks"]["link"]) == 1
    assert res["empty"] == {"text": "", "blocks": {"code": [], "link": [], "image": []}}
    assert res["nullText"] == {"text": "", "blocks": {"code": [], "link": [], "image": []}}
    assert "{{CUTTLE_" not in res["restored"]
    assert "message-code-block" in res["restored"]


FULL_PIPELINE_HARNESS = """
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const span = (s, e) => { const h = SRC.indexOf(s); const t = SRC.indexOf(e, h);
  if (h < 0 || t < 0) throw new Error('bad marker: ' + s.slice(0, 50)); return SRC.slice(h, t); };
const calls = {};
const rec = (n, fn) => (...a) => { (calls[n] = calls[n] || []).push(a.map((x) => String(x).slice(0, 80))); return fn(...a); };
// Real pure leaves (extracted, never copied).
eval(span('    function escapeHtmlInline(s) {', '    function windowsPathToFileUrl(path) {'));
eval(span('    function windowsPathToFileUrl(path) {', '    function normalizeMdHref(url) {'));
eval(span('    function normalizeMdHref(url) {', '    function isSafeMdHref(url) {'));
eval(span('    function isSafeMdHref(url) {', '    function mdLinkChipLabel(label, url) {'));
eval(span('    function mdLinkChipLabel(label, url) {', '    function renderMdLinkChip(label, url) {'));
eval(span('    function renderMdLinkChip(label, url) {', '    async function openFileInDefaultApp(href) {'));
eval(span('    function safeJsonParse(s) {', '    function slashCommandsForCurrentMode() {'));
eval(span('    function clamp(n, min, max) {', '    function safeJsonParse(s) {'));
eval(span('    function mediaKindFromUrl(url) {', '    function mediaPosterUrl(src) {'));
// Block/inline markdown + tables moved to chat_markdown.js (Slice 8E);
// the pipeline executes the real module, not page spans.
globalThis.CuttleChatMarkdown = require(process.env.MOD_MD);
globalThis.CuttleUsageLive = require(process.env.MOD_USAGE);
// escapeHtml is DOM-backed in the page; the equivalent inline escape is the
// documented premise (escape degrees are unit-covered, not pipeline-covered).
const escapeHtml = (t) => escapeHtmlInline(String(t ?? ''));
const structuredRenderDeps = () => ({ escapeHtmlInline, clamp,
  renderMdLinkChip, renderCuttlePricingHtml: (r) => '<price:' + r + '>',
  mediaKindFromUrl, buildMediaThumbHtml: (s, o) => '<thumb src="' + s + '">' });
// Owned-elsewhere leaves as labeled premises (own suites own internals;
// handle links have test_chat_handle_links.py).
const parseChatHandleToken = rec('handleTok', (t) => null);
const ingestAndStripCuttleWidgets = rec('ingest', (t) => t);
const linkifyChatHandlesInText = rec('handles', (t) => t);
const renderActionFormCard = rec('afcard', () => '<afcard>');
const renderCuttlePricingHtml = (r) => '<price:' + r + '>';
const buildMediaThumbHtml = (s, o) => '<thumb src="' + s + '">';
globalThis.CuttleChatMessages = require(process.env.MOD_JS);
globalThis.CuttleChatAttachments = require(process.env.CHAT_ATTACHMENTS_JS);
globalThis.window = { CuttleSupervised: {
    buildActivityDisclosureHtml: () => '<supact>', buildLiveIndicatorHtml: () => '<live>' },
  CuttleGitCommitViewer: { linkifyGitHashesInText: rec('git', (t) => t) } };
eval(span('    function formatMessage(text) {', '    function isTouchComposer() {'));
const MSG = ['Hey <think>because <b>reasons</b></think> done.',
  '```js', 'const a = 1;', '```',
  'See [docs](https://example.com/a) and https://example.com/b, plus CH-1x (not a handle).',
  '<cuttle_supervised_activity>{"status_text": "Working", "terminal": false}</cuttle_supervised_activity>',
  '<cuttle_trace>ran t</cuttle_trace>',
  '<progress id="p1" label="Loading" value="30"/> tail {{CUTTLE_FAKE_0}} end.',
  '<tool_output>plain output here</tool_output>'].join('\\n\\n');
const out = formatMessage(MSG);
const order = ['thinking-block', 'message-code-block', 'md-link-chip', 'example.com/b',
  '<supact>', 'pipeline-trace-block', 'data-progress-id="p1"', '{{CUTTLE_FAKE_0}}'];
const positions = order.map((s) => out.indexOf(s));
// Block-markdown message: headers, lists, table, quote, rule, placeholders.
const MSG2 = ['## Results *bold* and _em_ and `code`',
  '- alpha', '- beta', '', '1. one', '2. two', '',
  '| Name | Score |', '| --- | ---: |', '| Ann | 9 |', '| Bob | 7 | extra |', '',
  '> quoted **bold**', '', '---', '',
  '{{CUTTLE_CODE_0}}', '{{CUTTLE_FAKE_9}}',
  'not | a table', '| only one cell |'].join('\\n');
const out2 = formatMessage(MSG2);
process.stdout.write(JSON.stringify({ out, positions, calls, out2,
  linkPlaceholdersLeft: (out.match(/\\{\\{CUTTLE_LINK_\\d+\\}\\}/g) || []).length }));
"""


def _run_full_pipeline():
    import os
    proc = subprocess.run(
        ["node", "-e", FULL_PIPELINE_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "CHAT_PAGE_JS": str(CHAT_PAGE_JS),
             "CHAT_ATTACHMENTS_JS": str(CHAT_ATTACHMENTS_JS),
             "MOD_MD": str(MOD_MD), "MOD_USAGE": str(MOD_USAGE)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_format_message_pipeline_interleaves_all_stages():
    """Real formatMessage end to end: every planning stage restores in
    order, unknown placeholders pass through, nested markup inside an
    extracted block does not start a second block."""
    res = _run_full_pipeline()
    assert res["linkPlaceholdersLeft"] == 0
    assert all(p >= 0 for p in res["positions"]), res["positions"]
    assert res["positions"] == sorted(res["positions"])
    assert "&lt;b&gt;reasons" in res["out"]
    assert "const a = 1" in res["out"]
    assert "not a handle" in res["out"]
    assert "tool-block" in res["out"]
    assert "plain output here" in res["out"]
    assert res["out"].count("thinking-block") == 1
    assert res["calls"].get("handles") and res["calls"].get("git")
    second = res["out2"]
    assert '<h2 class="message-header">' in second
    assert "<strong>bold</strong>" in second and "<em>em</em>" in second
    assert "<code>code</code>" in second
    assert second.count("<li>") == 4 and "<ul>" in second and "<ol>" in second
    assert '<div class="message-table-wrap">' in second
    assert 'style="text-align:right"' in second
    assert "<td>extra</td>" in second
    assert "message-blockquote" in second
    assert '<hr class="message-hr">' in second
    # Unknown/fake and unresolvable placeholders pass through literally.
    assert "{{CUTTLE_FAKE_9}}" in second and "{{CUTTLE_CODE_0}}" in second
    assert "not | a table" in second and "| only one cell |" in second


def test_format_message_orders_structured_extraction_around_supervised():
    """Order pin: head planning → supervised (stays) → tail planning.

    The original bug class here is a take/dismiss-style hoist: running
    tail patterns before supervised placeholder-replacement would let
    live-activity JSON match block patterns. The page must keep the
    supervised seam between the two owned planning calls, and restore
    through the owned protocol instead of inline loops.
    """
    src = CHAT_PAGE_JS.read_text(encoding="utf-8")
    start = src.index("function formatMessage(text)")
    end = src.index("function isTouchComposer()", start)
    body = src[start:end]
    head = body.index("extractHeadStructuredBlocks")
    sup = body.index("supervisedActivityBlocks")
    tail = body.index("extractTailStructuredBlocks")
    assert head < sup < tail
    assert "restoreStructuredBlocks(" in body
    assert "structuredBlocks, { code: [], link: [] }" in body
    for leftover in (
        "pushThinkBlock",
        "extractThinkTags",
        "parseMediaAttr",
        "pushMediaCard",
        "pushVega",
        "const traceBlocks = []",
        "const toolBlocks = []",
        "CUTTLE_THINK_' + thinkBlocks.length",
    ):
        assert leftover not in body, f"moved planning must not remain inline: {leftover}"
    # supervised extraction + restore stay inline (live supervised state)
    assert "window.CuttleSupervised" in body
    assert "CUTTLE_SUP_ACT_' + supervisedActivityBlocks.length" in body


def test_page_delegates_message_history_decisions_to_owned_module():
    """Narrow page adapters: same signatures, no duplicated decision logic."""
    src = CHAT_PAGE_JS.read_text(encoding="utf-8")
    for adapter, owned in (
        ("function authMessageOptsFromServer(msg)",
         "CuttleChatMessages.authMessageOptsFromServer"),
        ("function preferredModelFromMessages(messages)",
         "CuttleChatMessages.preferredModelFromMessages"),
        ("function projectFromMessageRecord(msg)",
         "CuttleChatMessages.projectFromMessageRecord"),
        ("function annotateMessageProjects(messages)",
         "CuttleChatMessages.annotateMessageProjects"),
        ("function countVisibleChatMessages(messages)",
         "CuttleChatMessages.countVisibleChatMessages"),
        ("function windowChatHistoryMessages(data)",
         "CuttleChatMessages.windowHistoryMessages"),
        ("function applyChatHistoryPageMeta(data, messages)",
         "CuttleChatMessages.historyPageMeta"),
        ("function transcriptEndsWithAssistant(messages)",
         "CuttleChatMessages.transcriptEndsWithAssistant"),
        ("function buildVegaWrapHtml(rawSpec)",
         "CuttleChatMessages.buildVegaWrapHtml"),
        ("function cleanCodeCopyText(raw)",
         "CuttleChatMessages.cleanCodeCopyText"),
    ):
        assert adapter in src, f"page adapter {adapter} must stay (same signature)"
        assert owned in src, f"page must delegate to {owned}"


ORDER_HARNESS = """
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const span = (s, e) => { const h = SRC.indexOf(s); const t = SRC.indexOf(e, h);
  if (h < 0 || t < 0) throw new Error('bad marker: ' + s.slice(0, 50)); return SRC.slice(h, t); };
const CuttleChatMessages = require(process.env.MOD_JS);
eval(span('    function escapeHtmlInline(s) {', '    function windowsPathToFileUrl(path) {'));
eval(span('    function normalizeMdHref(url) {', '    function isSafeMdHref(url) {'));
eval(span('    function isSafeMdHref(url) {', '    function mdLinkChipLabel(label, url) {'));
eval(span('    function mdLinkChipLabel(label, url) {', '    function renderMdLinkChip(label, url) {'));
eval(span('    function renderMdLinkChip(label, url) {', '    function mediaKindFromUrl(url) {'));
const buildMediaThumbHtml = (src, opts) => '<img src="' + src + '">';
// --- real page extract call-site (module call + live linkChips bind) ---
let text = 'Talk {{CUTTLE_FORM_0}} here\\n```\\nvalue {{CUTTLE_FORM_0}} and {{CUTTLE_LINK_7}}\\n```';
const structuredBlocks = {};
eval(span('        const codeLinkStructured = CuttleChatMessages.extractCodeLinkBlocks(text, {',
           '        text = linkifyChatHandlesInText(text, linkChips);'));
// --- real page restore tail (bulk-redacted restore, then ordered loops) ---
const formatted = [text];
const supervisedActivityBlocks = [];
const formBlocks = ['<form>{{CUTTLE_CODE_0}}</form>'];
const actionFormBlocks = [];
const buttonBlocks = [];
var result = formatted.join('');
eval(span('        result = CuttleChatMessages.restoreStructuredBlocks(',
           '        return result;'));
const out = { result };
process.stdout.write(JSON.stringify(out));
"""


def _run_order():
    import os
    proc = subprocess.run(
        ["node", "-e", ORDER_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "CHAT_PAGE_JS": str(CHAT_PAGE_JS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_restore_pass_order_pinned_against_placeholder_interleaving():
    res = _run_order()["result"]
    # HEAD pass order: forms restore before code, so a form block carrying a
    # literal code token receives the code block (same as pre-change page).
    assert ("<form><pre class=\"message-code-block\"><code>value "
            "{{CUTTLE_FORM_0}} and {{CUTTLE_LINK_7}}</code></pre></form>") in res
    # The reverse direction stays protected: literal form/link tokens inside
    # fenced code are NOT substituted when the code chip restores.
    assert "value {{CUTTLE_FORM_0}} and {{CUTTLE_LINK_7}}" in res
    # The fenced block itself still restores exactly once per fence.
    assert res.count("message-code-block") == 2
    assert "{{CUTTLE_CODE_0}}" not in res


@node_only
def test_segmented_status_chip_types_tooltip_and_missing_reason():
    code = r'''
const A = require(process.env.MOD_JS);
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const out = {};
for (const kind of ['routed', 'escalation', 'fallback', 'default']) {
    out[kind] = A.renderRoutingBadgeHtml({kind, agent:'codex', reason:'Full <b>reason</b> & detail', initial_agent:'cursor', model:'model'},esc);
}
out.absent = A.renderRoutingBadgeHtml(null,esc);
out.noReason = A.renderRoutingBadgeHtml({kind:'routed',agent:'codex'},esc);
out.legacy = A.renderRoutingBadgeHtml({kind:'fallback',agent:'codex',reason:'Fallback codex / model after: transport unavailable'},esc);
out.agent = A.routingAgentSlash({agent:'codex',model:'model',effort:'high'});
out.unpinned = A.routingAgentSlash({agent:'cursor'});
out.error = A.renderAssistantStatusHtml({}, true, '[FAIL] Full error message\nOther text', esc);
out.healthy = A.renderAssistantStatusHtml({}, false, 'Mention an error in discussion', esc);
console.log(JSON.stringify(out));
'''
    import os
    result = subprocess.run(['node','-e',code],env={**os.environ, 'MOD_JS': str(MOD_JS)},capture_output=True,text=True, encoding="utf-8",check=True)
    out = json.loads(result.stdout)
    for kind, name in [('routed','Router'), ('escalation','Reroute'), ('fallback','Fallback'), ('default','Warning')]:
        html = out[kind]
        assert f'status-chip-name">{name}</span>' in html
        assert html.count('class="slash-chip-seg ') == 3
        assert '&lt;b&gt;reason&lt;/b&gt; &amp; detail' in html
        assert '<b>reason' not in html
        assert 'Initially selected:' not in html
        assert 'Codex ·' not in html
        assert 'tabindex="0"' in html
    assert out['absent'] == out['healthy'] == ''
    assert 'status-chip-message' in out['noReason']
    assert 'undefined' not in out['noReason']
    assert 'status-chip-name">Error</span>' in out['error']
    assert 'Other text' not in out['error']
    assert 'Fallback codex' not in out['legacy']
    assert 'transport unavailable' in out['legacy']
    assert out['agent']['chips'][0]['label'] == 'Codex - model · high'
    assert out['unpinned']['chips'][0]['label'] == 'Cursor'
