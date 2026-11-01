"""Transcript export (src/web/js/chat/chat_export.js): pure Markdown + filename under node."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "chat" / "chat_export.js"

HARNESS = r"""
const E = require(process.env.MOD);
const fmt = {
  messageOpts: (m) => Object.assign({ timestamp: m.ts || 0 }, m.meta || {}),
  stripAgentChrome: (t) => t.replace(/^\[chrome\] /, ''),
  normalizeSlashCommand: (sc) => sc || null,
  assistantName: () => 'Cuttle',
};
const at = new Date(2026, 10, 1, 9, 5).getTime();
const md = E.transcriptMarkdown({ title: '  Plan  ', displayId: 'CH-000042', now: new Date('2026-11-01T10:20:00Z'),
  messages: [
    { role: 'system', content: 'hidden' },
    { role: 'user', content: 'hi\nthere', ts: at },
    { role: 'assistant', content: '[chrome] done', meta: { routing_badge: { agent: 'codex' } } },
    { role: 'assistant', content: 'x', meta: { slash_command: { chips: [{ meta: '/claude opus' }] } } },
    { role: 'assistant', content: 'y', meta: { speaker: 'Scout' } },
    { role: 'assistant', content: 'z' },
    { role: 'assistant', content: '' },
  ] }, fmt);
console.log(JSON.stringify({
  md, untitled: E.transcriptMarkdown({ title: '', displayId: '7', messages: [], now: new Date(0) }, fmt),
  file: E.exportFilename('CH-000042', new Date('2026-11-01T10:20:00Z')),
  fileBlank: E.exportFilename('', new Date('2026-11-01T10:20:00Z')),
  names: [E.agentDisplayName('claude'), E.agentDisplayName('custom'), E.hhmm(0), E.hhmm('bad')],
}));
"""


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "MOD": str(MOD)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_transcript_markdown_rows_authors_and_header():
    r = _run()
    assert r["md"] == (
        "# Plan\nChat: CH-000042 · Exported 2026-11-01 10:20 UTC\n---\n\n"
        "**09:05 — You**\n\nhi\nthere\n\n"
        "**Codex**\n\ndone\n\n"
        "**Claude Code**\n\nx\n\n"
        "**Scout**\n\ny\n\n"
        "**Cuttle**\n\nz\n")
    assert r["untitled"].startswith("# Chat transcript\nChat: 7 · ")


def test_filename_and_names():
    r = _run()
    assert r["file"] == "cuttle-chat-ch-000042-20261101.md"
    assert r["fileBlank"] == "cuttle-chat-chat-20261101.md"
    assert r["names"] == ["Claude Code", "custom", "", ""]
