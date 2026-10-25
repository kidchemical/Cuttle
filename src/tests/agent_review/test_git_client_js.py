"""Shared Git-surface review client (``js/git/agent_review_client.js``) in node:
unified rows for the diff modal and per-repository matching. The DOM flows
(strip, modal, commit guard) run in ``e2e/test_agent_review_git_surfaces.py``."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

CLIENT = Path(__file__).resolve().parents[2] / "web" / "js" / "git" / "agent_review_client.js"

HARNESS = r"""
const C = require(process.env.CLIENT);
const review = {
  current: 'a\nb\nNEW\nc\nd\ne\nf\ng\nh\nZ\n',
  hunk_list: [
    {id: 'n-0', old_start: 3, old_count: 0, new_start: 3, new_count: 1, removed: [], added: ['NEW\n']},
    {id: 'r-0', old_start: 9, old_count: 1, new_start: 10, new_count: 1, removed: ['i\n'], added: ['Z\n']},
    {id: 'd-0', old_start: 1, old_count: 1, new_start: 1, new_count: 0, removed: ['gone\r\n'], added: []},
  ],
};
const out = {hunks: C.unifiedHunks(review, 2)};
const data = {repo_root: '/W/Proj/', files: [
  {path: 'x.py', rel_path: 'sub/x.py', hunks: 2}, {path: 'y.py', rel_path: 'sub/y.py', hunks: 0}]};
out.repo = [Object.keys(C.forRepo(data, '/w/proj')), Object.keys(C.forRepo(data, '/other')), Object.keys(C.forRepo(null, '/w'))];
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    if shutil.which("node") is None:
        pytest.skip("node unavailable")
    proc = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "CLIENT": str(CLIENT)})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _rows(hunk):
    return [(line["type"], line["text"], line.get("old_no"), line.get("new_no")) for line in hunk["lines"]]


def test_unified_hunks_add_context_without_repeating_lines(result):
    deletion, insert, replace = result["hunks"]  # sorted by position in the current file
    assert deletion["id"] == "d-0" and _rows(deletion) == [
        ("del", "gone", 1, None), ("context", "a", 2, 1), ("context", "b", 3, 2)]
    # Context before the insert was already shown by the previous hunk.
    assert _rows(insert) == [("add", "NEW", None, 3), ("context", "c", 3, 4), ("context", "d", 4, 5)]
    assert _rows(replace) == [("context", "g", 7, 8), ("context", "h", 8, 9),
                              ("del", "i", 9, None), ("add", "Z", None, 10)]
    assert replace["header"] == "@@ -9,1 +10,1 @@"


def test_for_repo_matches_the_panel_repository_only(result):
    assert result["repo"] == [["sub/x.py"], [], []]


FRAMES = r"""
// Several shell panes (separate pages) on one origin share a lock manager and
// a broadcast bus; only the lock holder may open the review stream.
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.env.CLIENT, 'utf8');
const waiters = [];
let held = false;
const locks = {request(name, cb) {
  return new Promise((resolve) => {
    const run = () => { held = true; Promise.resolve(cb()).then((v) => { held = false; resolve(v); const n = waiters.shift(); if (n) n(); }); };
    if (held) waiters.push(run); else run();
  });
}};
const bus = [];
class BC { constructor() { bus.push(this); }
  postMessage(data) { bus.filter((c) => c !== this).forEach((c) => c.onmessage && c.onmessage({data})); } close() {} }
const streams = [];
class ES { constructor(url) { this.url = url; streams.push(this); } close() { this.closed = true; } }
const fetch = async () => ({ok: true, json: async () => ({flags: [{id: 'agent_review', enabled: true}]})});
const frames = [];
for (let i = 0; i < 4; i++) {
  const win = {navigator: {locks}, BroadcastChannel: BC, EventSource: ES, fetch, document: {hidden: false},
               setInterval: () => 1};
  win.window = win;
  vm.runInNewContext(src, Object.assign(win, {console, URLSearchParams, Promise, JSON, Map, Set, Date}));
  const hits = [];
  win.CuttleAgentReview.subscribe(() => hits.push(1));
  frames.push(hits);
}
setTimeout(() => {
  const open = streams.filter((s) => !s.closed).length;
  streams[0].onmessage({data: JSON.stringify({revision: 1})});
  streams[0].onmessage({data: JSON.stringify({revision: 2})});
  const woke = frames.map((h) => h.length);
  streams[0].onerror();  // leader's stream dies → the next frame takes the lock
  setTimeout(() => {
    process.stdout.write(JSON.stringify({open, woke, total: streams.length}));
  }, 20);
}, 20);
"""


def test_review_stream_is_shared_across_frames():
    if shutil.which("node") is None:
        pytest.skip("node unavailable")
    proc = subprocess.run(["node", "-e", FRAMES], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "CLIENT": str(CLIENT)})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["open"] == 1  # four panes, one connection
    assert out["woke"] == [1, 1, 1, 1]  # revision change reached every pane
    assert out["total"] == 2  # handover: a waiting pane opened the replacement
