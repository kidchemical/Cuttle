"""Sent user bubble: a leading `/orchestrate @<playbook>` renders as the chip (Studio).

Node-level: loads chat_orchestrate_chip.js and chat_messages.js and drives the
user-bubble dispatch with stub leaves. Covers Studio on/off, the stored text
staying untouched, unknown ids, and the playbook name the shared cache resolves.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHIP_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_orchestrate_chip.js"
MESSAGES_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_messages.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

_RUNNER = r"""
// Stub before anything renders: node 22 has a global fetch that would hit a relative URL.
let fetchCalls = 0;
globalThis.fetch = async () => { fetchCalls += 1; return { json: async () => ({
  success: true, default_id: 'default',
  playbooks: [{ id: 'fast', name: 'Fast' }, { id: 'default', name: 'Default  -  org chart' }],
}) }; };
const chip = require(process.argv[1]);
const msgs = require(process.argv[2]);
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');
const deps = (studio) => ({
  normalizeAttachmentList: () => [],
  stripAttachedNote: (s) => s,
  inferAttachmentsFromContent: () => [],
  buildMessageAttachmentsHtml: () => '',
  parseButtonClickFromContent: () => null,
  formatFormReplyHtml: () => '',
  formatMessage: (s) => '<p>' + esc(s) + '</p>',
  parseStoredSlashCommandMessage: () => null,
  collapseCursorSlashChips: (c) => c,
  slashCommandChipHistoryHtml: (label, meta, cat) =>
    '<span class="slash-command-chip slash-command-chip--history" data-cat="' + esc(cat) + '" data-meta="'
    + esc(meta) + '"><span class="slash-chip-label">' + esc(label) + '</span></span>',
  escapeHtmlInline: esc,
  isStudio: () => studio,
});
const show = (text, studio) => msgs.formatUserMessageForDisplay(text, {}, deps(studio));
const out = {};
out.parse = {
  full: chip.bubblePrefixOf('/orchestrate @fast ship it'),
  bare: chip.bubblePrefixOf('/orchestrate @fast'),
  cmd: chip.bubblePrefixOf('/cmd orchestrate @fast go'),
  noAt: chip.bubblePrefixOf('/orchestrate ship it'),
  badId: chip.bubblePrefixOf('/orchestrate @<img> x'),
  notLeading: chip.bubblePrefixOf('hi /orchestrate @fast'),
};
out.studioOn = show('/orchestrate @fast ship it', true);
out.studioOff = show('/orchestrate @fast ship it', false);
out.studioBare = show('/orchestrate @fast', true);
out.studioNoAt = show('/orchestrate ship it', true);
out.studioBadId = show('/orchestrate @<img onerror=1> x', true);
out.plain = show('just talking', true);
out.labelUnknown = chip.bubbleLabel('zeta');
out.composerDefault = chip.labelFor({ prefix: '/orchestrate ' }, [{ id: 'default', name: 'Default org chart' }], 'default');
out.composerPinned = chip.labelFor({ prefix: '/orchestrate @fast ' }, [{ id: 'fast', name: 'Fast' }], 'default');
out.placeholderBeforeLoad = show('/orchestrate @fast ship it', true);
chip.ensurePlaybooks().then(() => {
  out.fetchCalls = fetchCalls;
  out.labelResolved = chip.bubbleLabel('fast');
  out.labelResolvedDefault = chip.bubbleLabel('default');
  out.afterLoad = show('/orchestrate @fast ship it', true);
  console.log(JSON.stringify(out));
});
"""


def _run() -> dict:
    proc = subprocess.run(
        ["node", "-e", _RUNNER, str(CHIP_JS), str(MESSAGES_JS)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(proc.stdout)


@node_only
def test_prefix_parse_keeps_head_and_body():
    out = _run()["parse"]
    assert out["full"] == {"head": "/orchestrate @fast", "playbookId": "fast", "body": "ship it"}
    assert out["bare"] == {"head": "/orchestrate @fast", "playbookId": "fast", "body": ""}
    assert out["cmd"] == {"head": "/cmd orchestrate @fast", "playbookId": "fast", "body": "go"}
    assert out["noAt"] is None
    assert out["badId"] is None
    assert out["notLeading"] is None


@node_only
def test_studio_renders_the_chip_and_keeps_the_body():
    out = _run()
    on = out["studioOn"]
    assert 'data-orch-playbook="fast"' in on
    assert 'data-meta="/orchestrate @fast"' in on
    assert 'data-cat="project-cmd"' in on
    assert '<div class="user-slash-body"><p>ship it</p></div>' in on


@node_only
def test_classic_keeps_the_raw_prefix():
    out = _run()
    assert out["studioOff"] == "<p>/orchestrate @fast ship it</p>"


@node_only
def test_bare_prefix_chip_has_no_body_div_and_other_text_is_untouched():
    out = _run()
    assert out["studioBare"].startswith('<div class="user-message-with-slash">')
    assert "user-slash-body" not in out["studioBare"]
    assert out["studioNoAt"] == "<p>/orchestrate ship it</p>"
    assert out["plain"] == "<p>just talking</p>"


@node_only
def test_invalid_playbook_text_stays_escaped_plain_text():
    out = _run()
    assert "<img" not in out["studioBadId"]
    assert out["studioBadId"] == "<p>/orchestrate @&lt;img onerror=1&gt; x</p>"


@node_only
def test_unknown_id_shows_plain_id_and_resolved_name_after_load():
    out = _run()
    assert out["labelUnknown"] == "Orchestrate - zeta"
    assert out["placeholderBeforeLoad"].count("Orchestrate - fast") == 1
    assert out["labelResolved"] == "Orchestrate - Fast"
    # Name cleanup matches the composer chip (`Name  -  x` becomes `Name x`).
    assert out["labelResolvedDefault"] == "Orchestrate - Default org chart"
    assert "Orchestrate - Fast" in out["afterLoad"]


@node_only
def test_playbook_list_loads_once_for_bubbles():
    assert _run()["fetchCalls"] == 1


@node_only
def test_composer_label_resolution_is_unchanged():
    out = _run()
    assert out["composerDefault"] == "Orchestrate - Default org chart"
    assert out["composerPinned"] == "Orchestrate - Fast"
