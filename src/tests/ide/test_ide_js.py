"""IDE App client: pure model (node) and shell/diff-modal wiring.

* node harness — tabs (preview semantics, MRU close, rename), fuzzy quick
  open, quick-diff line ranges, git decorations, chat prompts, persisted state
* static wiring — rail entry stashed + flag-gated + migrated, page title,
  diff modal Editor button gated on the flag and text files
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
MODEL = WEB / "js" / "ide" / "ide_model.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")

HARNESS = r"""
const M = require(process.env.MODEL);
const out = {};
let t = M.emptyTabs();
t = M.openTab(t, 'a.py', {preview: true});
t = M.openTab(t, 'b.py', {preview: true});
out.previewReplaced = t.tabs.map(x => x.path);
t = M.openTab(t, 'b.py');
t = M.openTab(t, 'c.py');
t = M.openTab(t, 'd.py', {preview: true});
out.order = t.tabs.map(x => x.path + (x.preview ? '*' : ''));
t = M.activateTab(t, 'b.py');
t = M.closeTab(t, 'b.py');
out.afterCloseActive = t.active;  // most recently used survivor
t = M.renameInTabs(t, 'c.py', 'lib/c.py');
out.renamed = t.tabs.map(x => x.path);
out.next = M.nextTab(t, 1).active;
out.labels = M.tabLabels({tabs: [{path: 'x/index.js'}, {path: 'y/index.js'}, {path: 'z.js'}]});
out.renamedPath = [M.renamedPath('src/a/b.js', 'src/a', 'lib'), M.renamedPath('src/ab.js', 'src/a', 'lib')];
out.fuzzy = M.fuzzyFilter('app', ['src/application/views.py', 'src/app.py', 'docs/a_p_p.md']).map(r => r.path);
out.fuzzyNone = M.fuzzyScore('zzz', 'src/app.py');
out.quick = [M.parseQuickOpen('app.py:12'), M.parseQuickOpen(':7'), M.parseQuickOpen('x')];
out.diff = {
  modified: M.lineDiff('a\nb\nc', 'a\nB\nc'),
  added: M.lineDiff('a\nc', 'a\nb1\nb2\nc'),
  deleted: M.lineDiff('a\nb\nc', 'a\nc'),
  newFile: M.lineDiff('', 'x\ny'),
  same: M.lineDiff('a\nb', 'a\nb'),
  tooBig: M.lineDiff('a\nb', 'c', {maxLines: 1}),
};
out.deco = M.decorationsFromPending({repos: [
  {repo_root: '/w', files: [{path: 'src/x.py', status: 'modified'}, {path: 'n.txt', status: 'untracked'}]},
  {repo_root: '/w/vendor/lib', files: [{path: 'y.c', status: 'deleted'}]},
]}, '/w');
out.repoRel = M.repoRelative({repos: [{repo_root: '/w'}, {repo_root: '/w/vendor/lib'}]}, '/w', 'vendor/lib/y.c');
out.preview = M.previewParts({preview: 'long line here', col: 6, length: 4, preview_offset: 0});
out.ctx = M.selectionContext({path: 'a.md', language: 'markdown', startLine: 2, endLine: 3, text: 'has ``` fence\n'});
out.edit = M.editPrompt({path: 'a.py', startLine: 4}, ' rename it ');
out.layout = M.normalizeLayout({view: 'bogus', sidebarWidth: 5, chat: 1});
out.ws = M.normalizeWorkspaceState({tabs: [{path: 'a'}, {nope: 1}, {path: '../b'}], active: 'zz', chatSession: '12', recent: ['a']});
out.canEdit = [M.canEditFromDiff({status: 'modified'}), M.canEditFromDiff({status: 'deleted'}), M.canEditFromDiff({binary: true}), M.canEditFromDiff(null)];
out.hl = M.highlightHtml('a<b', [0, 1]);
console.log(JSON.stringify(out));
"""


@node_only
def test_ide_model():
    proc = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True,
                          env={**os.environ, "MODEL": str(MODEL)}, check=True)
    out = json.loads(proc.stdout)
    assert out["previewReplaced"] == ["b.py"]
    assert out["order"] == ["b.py", "c.py", "d.py*"]
    assert out["afterCloseActive"] == "d.py"
    assert out["renamed"] == ["lib/c.py", "d.py"]
    assert out["next"] == "lib/c.py"
    assert out["labels"]["x/index.js"] == {"name": "index.js", "hint": "x"}
    assert out["labels"]["z.js"] == {"name": "z.js", "hint": ""}
    assert out["renamedPath"] == ["lib/b.js", "src/ab.js"]
    assert out["fuzzy"][0] == "src/app.py"
    assert out["fuzzyNone"] is None
    assert out["quick"][0] == {"query": "app.py", "line": 12, "col": None, "gotoLineOnly": False}
    assert out["quick"][1]["gotoLineOnly"] is True and out["quick"][1]["line"] == 7
    d = out["diff"]
    assert d["modified"] == [{"type": "modified", "start": 2, "end": 2}]
    assert d["added"] == [{"type": "added", "start": 2, "end": 3}]
    assert d["deleted"] == [{"type": "deleted", "start": 1, "end": 1}]
    assert d["newFile"] == [{"type": "added", "start": 1, "end": 2}]
    assert d["same"] == [] and d["tooBig"] is None
    assert out["deco"]["files"] == {"src/x.py": "M", "n.txt": "U", "vendor/lib/y.c": "D"}
    assert out["deco"]["dirs"] == {"src": True, "vendor/lib": True, "vendor": True}
    assert out["repoRel"] == {"repoRoot": "/w/vendor/lib", "file": "y.c"}
    assert out["preview"] == {"before": "long ", "match": "line", "after": " here"}
    assert out["ctx"].startswith("`a.md` (lines 2–3):\n````markdown\nhas ``` fence\n````")
    assert out["edit"].startswith("In `a.py` at line 4\n\nrename it\n")
    assert out["layout"]["view"] == "explorer" and out["layout"]["sidebarWidth"] == 160 and out["layout"]["chat"] is True
    assert out["ws"]["tabs"] == [{"path": "a", "preview": False}]
    assert out["ws"]["active"] == "a" and out["ws"]["chatSession"] == "12"
    assert out["canEdit"] == [True, False, False, False]
    assert out["hl"] == "<b>a&lt;</b>b"


def test_rail_entry_is_stashed_flag_gated_and_migrated():
    html = (WEB / "app_shell.html").read_text(encoding="utf-8")
    assert 'id="nav-ide"' in html and 'data-page="/ide_page.html"' in html
    rail = (WEB / "js" / "shell" / "shell_rail.js").read_text(encoding="utf-8")
    assert "'nav-ide'" in rail.split("const CANONICAL_RAIL_ITEM_ORDER")[1].split("];")[0]
    assert "'nav-ide'" in rail.split("const DEFAULT_RAIL_HIDDEN =")[1].split(";")[0]
    assert "'nav-ide': 'ide'" in rail.split("const EXPERIMENTAL_RAIL_APPS")[1].split("};")[0]
    assert "version >= 10 ? ['nav-ide']" in rail
    shell = (WEB / "js" / "shell" / "app_shell.js").read_text(encoding="utf-8")
    assert "'/ide_page.html': 'IDE'" in shell


def test_diff_modal_editor_button_is_gated():
    js = (WEB / "js" / "git" / "diff_modal.js").read_text(encoding="utf-8")
    assert 'data-diff="editor"' in js and "hidden>Editor</button>" in js
    assert "f.id === 'ide' && f.enabled" in js and "!d.kill_switch" in js
    assert "statusLabel(data.status) !== 'D'" in js and "!data.binary" in js
    page = (WEB / "ide_page.html").read_text(encoding="utf-8")
    for script in ("ide_model.js", "ide_api.js", "ide_theme.js", "ide_editor.js", "ide_quick.js", "ide_menubar.js",
                   "ide_navigation.js", "ide_explorer.js", "ide_search.js", "ide_terminal.js", "ide_chat.js",
                   "ide_page.js"):
        assert f"/js/ide/{script}?v=" in page
    assert page.index("ide_model.js") < page.index("ide_menubar.js") < page.index("ide_page.js")
