"""Messages/History frontend domain.

Behavioral characterization of src/web/js/chat_messages.js under node
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

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat_messages.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"

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
        capture_output=True, text=True, timeout=30,
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
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


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
    ):
        assert adapter in src, f"page adapter {adapter} must stay (same signature)"
        assert owned in src, f"page must delegate to {owned}"
