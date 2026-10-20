"""IDE agent review client: pure model in node + static wiring.

The controller and Monaco rendering are covered in a real browser by
``src/tests/e2e/test_ide_agent_review.py``.
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
MODEL = WEB / "js" / "ide" / "ide_review_model.js"

HARNESS = r"""
const R = require(process.env.MODEL);
const hunks = [
  {id: 'a-0', old_start: 1, old_count: 0, new_start: 1, new_count: 1, removed: [], added: ['# head\n']},
  {id: 'b-0', old_start: 2, old_count: 1, new_start: 3, new_count: 1, removed: ["  return 'hi'\n"], added: ["  return 'yo'\n"]},
  {id: 'c-0', old_start: 5, old_count: 2, new_start: 6, new_count: 0, removed: ['x\r\n', 'y\n'], added: []},
];
const items = R.layout(hunks);
const out = {items};
out.next = [R.nextHunk(items, 1, 1).id, R.nextHunk(items, 3, 1).id, R.nextHunk(items, 9, 1).id];
out.prev = [R.nextHunk(items, 9, -1).id, R.nextHunk(items, 1, -1).id];
out.after = [R.hasHunkAfter(items, 5, 1), R.hasHunkAfter(items, 1, -1)];
out.at = [R.hunkAt(items, 3).id, R.hunkAt(items, 6).id, R.hunkAt(items, 2).id];
out.none = R.nextHunk([], 1, 1);
const idx = R.index([
  {path: 'src/a.py', hunks: 2, additions: 3, deletions: 1, live: true, change: 'modified'},
  {path: 'b.md', hunks: 1, additions: 1, deletions: 0, change: 'added'},
  {path: 'src/deep/c.js', hunks: 4, additions: 0, deletions: 4, change: 'deleted'},
  {path: 'gone.txt', hunks: 0},
]);
out.idx = idx;
out.files = [R.nextFile(idx, 'b.md', 1), R.nextFile(idx, 'b.md', -1), R.nextFile(idx, 'zzz', 1),
             R.nextFile(R.index([{path: 'only', hunks: 1}]), 'only', 1)];
out.summary = [R.summary(idx.totals), R.summary(R.index([{path: 'x', hunks: 1, additions: 1}]).totals), R.summary({})];
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    if shutil.which("node") is None:
        pytest.skip("node unavailable")
    proc = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, encoding="utf-8", timeout=30,
                          env={**os.environ, "MODEL": str(MODEL)})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_layout_places_removed_lines_and_actions(result):
    head, change, deletion = result["items"]
    assert head["added"] == {"start": 1, "end": 1} and head["removed"] == [] and head["actionAfter"] == 1
    assert change["zoneAfter"] == 2 and change["removed"] == ["  return 'hi'"] and change["actionAfter"] == 3
    # A pure deletion renders after the line before it; endings are stripped.
    assert deletion["added"] is None and deletion["zoneAfter"] == 5 and deletion["removed"] == ["x", "y"]
    assert deletion["anchor"] == 5 and deletion["deletions"] == 2


def test_navigation_wraps_and_finds_the_change_under_the_cursor(result):
    assert result["next"] == ["b-0", "c-0", "a-0"]
    assert result["prev"] == ["c-0", "c-0"]
    assert result["after"] == [False, False]
    assert result["at"] == ["b-0", "c-0", "b-0"]
    assert result["none"] is None


def test_index_counts_files_and_parent_dirs(result):
    idx = result["idx"]
    assert idx["order"] == ["b.md", "src/a.py", "src/deep/c.js"]
    assert idx["dirs"] == {"src": 6, "src/deep": 4}
    assert idx["totals"] == {"files": 3, "hunks": 7, "additions": 4, "deletions": 5, "live": True}
    assert idx["files"]["b.md"]["change"] == "added" and "gone.txt" not in idx["files"]
    assert result["files"] == ["src/a.py", "src/deep/c.js", "b.md", None]
    assert result["summary"] == ["3 files · 7 changes · +4 −5", "1 file · 1 change · +1 −0", ""]


def test_page_loads_review_owners_and_keeps_the_root_thin():
    html = (WEB / "ide_page.html").read_text(encoding="utf-8")
    scripts = [line for line in html.splitlines() if "<script" in line]
    order = [next(i for i, s in enumerate(scripts) if name in s)
             for name in ("ide_review_model.js", "ide_editor.js", "ide_review.js", "ide_page.js")]
    assert order == sorted(order)
    assert 'id="ideReviewBar"' in html and 'id="ideStatusReview"' in html
    page = (WEB / "js" / "ide" / "ide_page.js").read_text(encoding="utf-8")
    assert "CuttleIdeReview.create(" in page
    # Hunk logic and server calls stay in the review owners.
    assert "reviewAct" not in page and "R.layout" not in page and "/api/agent-review" not in page
