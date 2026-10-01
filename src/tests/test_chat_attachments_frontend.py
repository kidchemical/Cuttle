"""Attachments frontend domain: classification, notes, upload plans.

Behavioral characterization of src/web/js/chat_attachments.js under
node (Phase 3 Slice 5). Covers image/file classification, item
normalization, `[Attached: …]` note strip/parse, legacy inference
from history text, outbound note formatting, and upload-result
planning against the `/api/upload` contract ({success, files} vs
{success: False, error}). Chip rendering, pending-state persistence,
upload transport, message HTML, and send orchestration stay in
chat_page.js; the vision pre-pass backend is covered by
test_chat_attachments.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat_attachments.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
// classification
out.imgMime = A.isImageAttachment({ mime: 'image/png', filename: 'a.bin' });
out.imgExt = A.isImageAttachment({ filename: 'photo.JPG' });
out.imgUrl = A.isImageAttachment({ url: '/output/uploads/1/a.webp' });
out.imgPdf = A.isImageAttachment({ mime: 'application/pdf', filename: 'a.pdf' });
out.imgNull = A.isImageAttachment(null);
out.imgEmpty = A.isImageAttachment({});
out.imgTxt = A.isImageAttachment({ filename: 'notes.txt' });
// normalization
out.norm = A.normalizeAttachmentList([
  { filename: 'a.png', path: '/p/a.png', mime: 'image/png', size: 3, url: '/output/u/a.png' },
  null, 'x',
  { path: '/p/b' },
]);
out.normNull = A.normalizeAttachmentList(null);
out.normStr = A.normalizeAttachmentList('nope');
// history notes
out.strip = A.stripAttachedNote('hello\\n[Attached: a.png, b.pdf]');
out.stripNone = A.stripAttachedNote('plain text');
out.stripMid = A.stripAttachedNote('[Attached: a.png] trailing');
out.stripEmpty = A.stripAttachedNote('');
out.parse = A.parseAttachedFilenames('hi\\n[Attached: a.png,  b.pdf ]');
out.parseNone = A.parseAttachedFilenames('no note here');
out.parseMid = A.parseAttachedFilenames('[Attached: a.png] and more');
// legacy inference
out.inferSess = A.inferAttachmentsFromContent(
  { content: 'hi\\n[Attached: a.png, b.pdf]', sessionId: '182' });
out.inferAnon = A.inferAttachmentsFromContent(
  { content: '[Attached: a.png]', sessionId: null });
out.inferNone = A.inferAttachmentsFromContent({ content: 'plain', sessionId: '182' });
out.inferImg = A.inferAttachmentsFromContent(
  { content: '[Attached: x.txt]', sessionId: '' });
// outbound formatting
out.fmtBoth = A.formatMessageWithAttachments('  hi  ',
  [{ filename: 'a.png' }, { filename: 'b.pdf' }]);
out.fmtNoteOnly = A.formatMessageWithAttachments('', [{ filename: 'a.png' }]);
out.fmtNone = A.formatMessageWithAttachments('hi', []);
out.fmtNull = A.formatMessageWithAttachments('hi', null);
out.fmtMissing = A.formatMessageWithAttachments('', [{}]);
// upload plans
out.upOk = A.uploadResultPlan({ ok: true, status: 200, data: { success: true, files: [
  { filename: 'a.png', path: '/p', mime: 'image/png', size: 3, url: '/u/a.png' } ] } });
out.upErr = A.uploadResultPlan({ ok: false, status: 400,
  data: { success: false, error: 'Unsupported file type: .exe. Use images or PDF.' } });
out.upHttp = A.uploadResultPlan({ ok: false, status: 500, data: null });
out.upEmpty = A.uploadResultPlan({ ok: true, status: 200,
  data: { success: false, error: 'No valid files uploaded' } });
// pending-list transitions
out.addOrder = A.addAttachmentsToPending([{ filename: 'a.png' }],
  [{ filename: 'b.pdf' }, { filename: 'a.png' }]);
out.addNull = A.addAttachmentsToPending(null, [{ filename: 'a.png' }]);
out.addBad = A.addAttachmentsToPending([{ filename: 'a.png' }], [null, 'x']);
out.remMid = A.removeAttachmentAt([{ filename: 'a' }, { filename: 'b' }, { filename: 'c' }], 1);
out.remBad = A.removeAttachmentAt([{ filename: 'a' }], 5);
out.remNeg = A.removeAttachmentAt([{ filename: 'a' }], -1);
// request payload + display-kind decisions
out.req = A.requestAttachmentPayload([
  { filename: 'a.png', path: '/p', mime: 'image/png', size: 9, url: '/u' }, null]);
out.kindThumb = A.messageAttachmentKind({ mime: 'image/png', url: '/u/a.png' });
out.kindLink = A.messageAttachmentKind({ mime: 'application/pdf', url: '/u/b.pdf' });
out.kindSpan = A.messageAttachmentKind({ filename: 'c.pdf' });
out.kindImgNoUrl = A.messageAttachmentKind({ mime: 'image/png' });
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
def test_image_classification():
    res = _run()
    assert res["imgMime"] is True  # mime wins over misleading name
    assert res["imgExt"] is True
    assert res["imgUrl"] is True
    assert res["imgPdf"] is False  # PDFs are files, never thumbs
    assert res["imgNull"] is False
    assert res["imgEmpty"] is False
    assert res["imgTxt"] is False


@node_only
def test_item_normalization():
    res = _run()
    assert res["norm"] == [
        {"filename": "a.png", "path": "/p/a.png", "mime": "image/png",
         "size": 3, "url": "/output/u/a.png"},
        {"filename": "file", "path": "/p/b", "mime": "",
         "url": ""},  # absent size stays absent (mirrors page behavior)
    ]
    assert res["normNull"] == []
    assert res["normStr"] == []


@node_only
def test_history_note_strip_and_parse():
    res = _run()
    assert res["strip"] == "hello"
    assert res["stripNone"] == "plain text"
    assert res["stripMid"] == "[Attached: a.png] trailing"  # mid-text notes stay
    assert res["stripEmpty"] == ""
    assert res["parse"] == ["a.png", "b.pdf"]
    assert res["parseNone"] == []
    assert res["parseMid"] == []


@node_only
def test_legacy_inference_from_history_text():
    res = _run()
    sess = res["inferSess"]
    assert [i["filename"] for i in sess] == ["a.png", "b.pdf"]
    assert sess[0]["url"] == "/output/uploads/182/a.png"
    assert sess[0]["mime"] == "image/*"
    assert sess[1]["mime"] == ""
    anon = res["inferAnon"]
    assert anon[0]["url"] == "/output/uploads/anon/a.png"
    assert res["inferNone"] == []
    assert res["inferImg"][0]["mime"] == ""  # non-image by extension
    assert res["inferImg"][0]["url"] == "/output/uploads/anon/x.txt"


@node_only
def test_outbound_note_formatting():
    res = _run()
    assert res["fmtBoth"] == "hi\n[Attached: a.png, b.pdf]"
    assert res["fmtNoteOnly"] == "[Attached: a.png]"
    assert res["fmtNone"] == "hi"
    assert res["fmtNull"] == "hi"
    assert res["fmtMissing"] == "[Attached: file]"


@node_only
def test_upload_result_plans():
    res = _run()
    assert res["upOk"] == {"ok": True, "items": [
        {"filename": "a.png", "path": "/p", "mime": "image/png",
         "size": 3, "url": "/u/a.png"}]}
    assert res["upErr"] == {"ok": False,
                            "error": "Unsupported file type: .exe. Use images or PDF."}
    assert res["upHttp"] == {"ok": False, "error": "Upload failed (HTTP 500)"}
    assert res["upEmpty"] == {"ok": False, "error": "No valid files uploaded"}


@node_only
def test_pending_list_and_payload_mapping():
    res = _run()
    # order preserved, duplicates kept (matches historical push behavior)
    assert [i["filename"] for i in res["addOrder"]] == ["a.png", "b.pdf", "a.png"]
    assert [i["filename"] for i in res["addNull"]] == ["a.png"]
    assert [i["filename"] for i in res["addBad"]] == ["a.png"]
    assert [i["filename"] for i in res["remMid"]] == ["a", "c"]
    assert [i["filename"] for i in res["remBad"]] == ["a"]
    assert [i["filename"] for i in res["remNeg"]] == ["a"]
    # request payload drops size, keeps the server-resolved fields
    assert res["req"] == [{"filename": "a.png", "path": "/p",
                           "mime": "image/png", "url": "/u"}]
    assert res["kindThumb"] == "thumb"
    assert res["kindLink"] == "link"
    assert res["kindSpan"] == "span"
    assert res["kindImgNoUrl"] == "span"  # image without URL cannot thumb


@node_only
def test_chat_attachments_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
