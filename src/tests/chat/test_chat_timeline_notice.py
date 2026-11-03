"""Timeline notices (chat_timeline_notice.js) — pure spec helpers under node.

One owner for the transcript's "something happened" rows (project / worktree /
stop / model / compacted). Covers spec normalization, legacy-text parsing for
rows saved before the structured metadata existed, readable persisted text,
markup, and the compact gauge's before/after detail.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_timeline_notice.js"
GAUGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_context_gauge.js"
PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_page.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

HARNESS = r"""
const N = require(process.env.MOD_JS);
const G = require(process.env.GAUGE_JS);
const pick = (p) => ({ type: p.type, state: p.state, icon: p.icon, label: p.label, detail: p.detail });
const out = {};
out.legacy = {
  project: pick(N.fromText('📁 Project: Cuttle → `/mnt/warpgate/Dev/Cuttle`')),
  worktree: pick(N.fromText('\u{1F33F} Worktree: feat/x')),
  checkout: pick(N.fromText('📁 Local checkout')),
  stop: pick(N.fromText('⏹ Stopped generating.', 'generation-stop')),
  model: pick(N.fromText('⚡ Model: GPT-5', 'model-change')),
  plain: pick(N.fromText('Something odd')),
};
out.structured = pick(N.fromRow('ignored', 'context-compact', {
  timeline_notice: { type: 'compact', label: 'Claude context compacted', detail: '82% → 14%', state: 'running' } }));
out.savedFallsBackToText = pick(N.fromRow('⚡ Model: Opus', 'model-change', { timeline_notice: { label: '' } }));
out.unknownType = pick(N.normalize({ type: 'nope', label: 'x' }));
out.text = {
  stop: N.toText({ type: 'stop', label: 'Stopped generating.' }),
  project: N.toText({ type: 'project', label: 'Project: Cuttle', detail: '/p' }),
  compact: N.toText({ type: 'compact', label: 'Claude context compacted', detail: '82% → 14%' }),
};
out.roundTrip = pick(N.fromText(N.toText({ type: 'compact', label: 'Claude context compacted', detail: '82% → 14%' }), 'context-compact'));
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
out.html = {
  done: N.renderHtml({ type: 'project', label: 'Project: <b>', detail: '/p' }, esc),
  running: N.renderHtml({ type: 'compact', state: 'running', label: 'Compacting…' }, esc),
  error: N.renderHtml({ type: 'compact', state: 'error', label: 'Compact failed', detail: 'boom' }, esc),
};
out.elapsed = [0, 5, 59, 61, 600].map(N.elapsedText);
out.compactDetail = [G.compactDetail(82, 14), G.compactDetail(null, 14), G.compactDetail(82, null), G.compactDetail(null, null)];
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out():
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"MOD_JS": str(MOD_JS), "GAUGE_JS": str(GAUGE_JS), "PATH": shutil.os.environ.get("PATH", "")},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_legacy_text_rows_parse_into_typed_notices(out):
    lg = out["legacy"]
    assert lg["project"] == {"type": "project", "state": "done", "icon": "📁",
                             "label": "Project: Cuttle", "detail": "/mnt/warpgate/Dev/Cuttle"}
    assert lg["worktree"]["type"] == "worktree" and lg["worktree"]["label"] == "Worktree: feat/x"
    assert lg["checkout"]["type"] == "checkout"
    assert lg["stop"] == {"type": "stop", "state": "done", "icon": "⏹", "label": "Stopped generating.", "detail": ""}
    assert lg["model"]["type"] == "model" and lg["model"]["label"] == "Model: GPT-5"
    assert lg["plain"] == {"type": "notice", "state": "done", "icon": "", "label": "Something odd", "detail": ""}


@node_only
def test_saved_metadata_wins_and_restores_settled(out):
    # A persisted row never comes back spinning.
    assert out["structured"] == {"type": "compact", "state": "done", "icon": "🗜️",
                                 "label": "Claude context compacted", "detail": "82% → 14%"}
    assert out["savedFallsBackToText"]["label"] == "Model: Opus"
    assert out["unknownType"]["type"] == "notice"


@node_only
def test_persisted_text_stays_readable_and_round_trips(out):
    # Agents, the history CLI and the stop e2e suites read this exact text.
    assert out["text"]["stop"] == "⏹ Stopped generating."
    assert out["text"]["project"] == "📁 Project: Cuttle · /p"
    assert out["text"]["compact"] == "🗜️ Claude context compacted · 82% → 14%"
    assert out["roundTrip"]["label"] == "Claude context compacted"
    assert out["roundTrip"]["detail"] == "82% → 14%"


@node_only
def test_markup_states(out):
    done, running, error = out["html"]["done"], out["html"]["running"], out["html"]["error"]
    assert "system-message timeline-notice timeline-notice--project is-done" in done
    assert "timeline-notice--code-detail" in done
    assert "&lt;b>" in done and "<b>" not in done
    assert 'role="status"' in running and "timeline-notice-spinner" in running
    assert "timeline-notice-elapsed" in running and "timeline-notice-icon" not in running
    assert "is-error" in error and "boom" in error


@node_only
def test_elapsed_and_compact_detail(out):
    assert out["elapsed"] == ["", "5s", "59s", "1m 01s", "10m 00s"]
    assert out["compactDetail"] == ["82% → 14%", "now 14%", "", ""]


def test_page_routes_every_notice_through_the_owner():
    src = PAGE_JS.read_text(encoding="utf-8")
    start = src.index("    function addSystemMessage(message, opts = {}) {")
    body = src[start:src.index("\n    }\n", start)]
    assert "timelineNotices().add(message, opts)" in body
    assert "CuttleChatTimelineNotice.create(" in src
    assert "class=\"message-content system-message\"" not in src
    # Callers pass typed specs, not emoji-prefixed strings.
    assert "`📁 Project:" not in src and "\\u{1F33F} Worktree" not in src
    assert "ensureGenerationStopNotice('⏹" not in src
