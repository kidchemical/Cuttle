"""Chat git-commit chips reuse the Git page commit viewer."""

from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
VIEWER_JS = WEB / "js" / "git_commit_viewer.js"
GRAPH_JS = WEB / "js" / "git_graph_page.js"
CHAT_JS = WEB / "js" / "chat_page.js"
CHAT_HTML = WEB / "chat_page.html"
GRAPH_HTML = WEB / "git_graph_page.html"


def test_shared_viewer_exports_and_detail_markup():
    src = VIEWER_JS.read_text(encoding="utf-8")
    assert "CuttleGitCommitViewer" in src
    assert "function renderDetailHTML" in src
    assert "git-graph-file-btn" in src
    assert "function linkifyGitHashesInText" in src
    assert "openFromChat" in src
    assert "8-char" in src


def test_git_graph_reuses_shared_viewer():
    src = GRAPH_JS.read_text(encoding="utf-8")
    assert "CuttleGitCommitViewer.create" in src
    assert "function renderDetail(" not in src, (
        "Git page must render commit detail via CuttleGitCommitViewer, not a local copy"
    )
    assert "function openCommitDiffModal" not in src
    html = GRAPH_HTML.read_text(encoding="utf-8")
    assert "git_commit_viewer.js" in html


def test_chat_wires_hash_chips_and_modals():
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "linkifyGitHashesInText" in js
    assert "bindGitCommitLinkClicks" in js
    assert "initGitCommitViewer" in js
    assert "openFromChat" in js
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert "git_commit_viewer.js" in html
    assert "git_commit_viewer.css" in html
    assert 'id="gitCommitModal"' in html
    assert 'diff_modal.js' in html
    assert 'diff_modal.css' in html
    assert 'id="gitDiffModal"' not in html


def test_linkify_hash_rules_via_node():
    """7-char and backticked hashes become chips; bare 8-char query ids do not."""
    import json
    import subprocess

    viewer = json.dumps(str(VIEWER_JS))
    script = f"""
const fs = require('fs');
const vm = require('vm');
const ctx = {{ window: {{}}, console }};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({viewer}, 'utf8'), ctx);
const V = ctx.window.CuttleGitCommitViewer;
if (!V) throw new Error('missing CuttleGitCommitViewer');
if (!V.shouldLinkifyBareGitHash('652311f')) throw new Error('7-char bare');
if (V.shouldLinkifyBareGitHash('e56d915e')) throw new Error('8-char bare should skip');
if (!V.shouldLinkifyBareGitHash('a'.repeat(40))) throw new Error('40-char bare');
if (!V.shouldLinkifyInlineCodeGitHash('e56d915e')) throw new Error('8-char backtick');
if (V.shouldLinkifyBareGitHash('1234567')) throw new Error('digits-only');
const chips = [];
let out = V.linkifyGitHashesInText('see 652311f and `e56d915e` plus e56d915e done', chips);
if (chips.length !== 2) throw new Error('chips=' + chips.length + ' out=' + out);
const joined = chips.join(' | ');
if (!joined.includes('data-git-hash="652311f"')) throw new Error(joined);
if (!joined.includes('data-git-hash="e56d915e"')) throw new Error(joined);
if (!out.includes('plus e56d915e done')) throw new Error('bare 8-char was eaten: ' + out);
const html = V.renderDetailHTML({{
  hash: 'abc1234deadbeef',
  short: 'abc1234',
  subject: 'fix spinner',
  author: 'kc',
  date: 'now',
  parents: [],
  files: [{{status: 'M', path: 'src/web/js/chat_page.js'}}],
}});
if (!html.includes('git-graph-file-btn')) throw new Error('file button missing');
if (!html.includes('src/web/js/chat_page.js')) throw new Error('path missing');
console.log('ok');
"""
    r = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr or r.stdout
    assert "ok" in r.stdout
