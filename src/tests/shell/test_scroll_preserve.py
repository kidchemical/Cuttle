"""Shared scroll preservation across innerHTML swaps (poll-refresh guard)."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
SHARED = WEB / "js" / "shared" / "scroll_preserve.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


@node_only
def test_set_html_preserves_scroller_offsets():
    script = "const assert = require('node:assert/strict');\n"
    script += SHARED.read_text(encoding="utf-8") + "\n"
    script += """
function scroller(left, top) { return { scrollLeft: left, scrollTop: top }; }
const root = {
    _kids: [scroller(120, 40)],
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; this._kids = [scroller(0, 0)]; },
    querySelectorAll() { return this._kids; },
};
const changed = CuttleScroll.setHtml(root, '<table></table>', { selector: '.x' });
assert.equal(changed, true);
assert.equal(root._kids[0].scrollLeft, 120, 'horizontal scroll restored');
assert.equal(root._kids[0].scrollTop, 40, 'vertical scroll restored');
// No-op when markup is unchanged.
root._html = '<table></table>';
assert.equal(CuttleScroll.setHtml(root, '<table></table>', { selector: '.x' }), false);
// Fallback: no matching descendant -> the element itself is the scroller.
const self = {
    scrollLeft: 55, scrollTop: 7, innerHTML: '',
    querySelectorAll() { return []; },
};
CuttleScroll.setHtml(self, '<p>new</p>', { selector: '.nothing' });
assert.equal(self.scrollLeft, 55);
assert.equal(self.scrollTop, 7);
"""
    subprocess.run(["node", "-e", script], check=True)


def test_jobs_page_uses_scroll_preserve():
    html = (WEB / "jobs_page.html").read_text(encoding="utf-8")
    assert "/js/shared/scroll_preserve.js" in html
    js = (WEB / "js" / "jobs" / "jobs_page.js").read_text(encoding="utf-8")
    assert "CuttleScroll.setHtml" in js
    assert ".jobs-table-scroll" in js


def test_pending_changes_panel_preserves_list_scroll():
    js = (WEB / "js" / "git" / "pending_changes_panel.js").read_text(encoding="utf-8")
    assert "list.scrollTop = listTop" in js
    assert "list.scrollLeft = listLeft" in js
