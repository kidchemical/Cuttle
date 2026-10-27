"""Per-frame and per-sync cost of the chat page (Host typing lag, 2026-10-25).

With ~1k chats, every history sync looked up each row against the full
session list with a linear find (O(rows × sessions)): ~125 ms long tasks
every activity snapshot, per pane, which stalled keystrokes for seconds.
The working bubble's waveform animated ``height`` (a layout per frame) and
the running-row glow animated ``box-shadow`` (a repaint per row per frame).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_activity.js"
PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_page.js"
CHAT_CSS = REPO_ROOT / "src" / "web" / "css" / "chat_page.css"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

HARNESS = r"""
const A = require(process.env.MOD_JS);
const linear = (list, id) => list.find((x) => x && A.sessionIdsEqual(x.id, id)) || null;
const ids = ['182', 182, 'CH-000182', 'db_session_182', '0182', 'anon-abc', 'CH-xyz', 'xyz', '7', 'missing', 'CH-000007-3'];
const list = [{id: 'anon-abc'}, null, {id: 7}, {id: 'db_session_182'}, {id: '182'}, {}, {id: 'xyz'}, {id: 'anon-abc', dup: 1}];
const out = {mismatch: []};
for (const id of ids) {
  if (A.findSessionById(list, id) !== linear(list, id)) out.mismatch.push(String(id));
}
out.nullList = A.findSessionById(null, '1');
out.nullId = A.findSessionById(list, null);
// Replaced arrays get their own index.
const next = [{id: '9'}];
out.replaced = (A.findSessionById(next, 'CH-000009') || {}).id;
// Scale: 2000 rows looked up against 2000 sessions.
const big = Array.from({length: 2000}, (_, i) => ({id: String(i + 1)}));
const t0 = Date.now();
let hits = 0;
for (let i = 0; i < 2000; i++) if (A.findSessionById(big, 'CH-' + String(i + 1).padStart(6, '0'))) hits++;
out.hits = hits;
out.ms = Date.now() - t0;
process.stdout.write(JSON.stringify(out));
"""


@node_only
def test_indexed_session_lookup_matches_linear_find_and_scales():
    proc = subprocess.run(
        ["node", "-e", HARNESS], capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["mismatch"] == []
    assert res["nullList"] is None and res["nullId"] is None
    assert res["replaced"] == "9"
    assert res["hits"] == 2000
    # Linear find took seconds here; the index is a few milliseconds.
    assert res["ms"] < 500, res["ms"]


def test_history_sync_paths_use_indexed_lookup():
    src = PAGE_JS.read_text(encoding="utf-8")
    for name in ("findAuthServerSession", "authSessionListHas", "isChatSessionStarred"):
        body = src.split(f"function {name}(", 1)[1].split("\n    function ", 1)[0]
        assert "CuttleChatActivity.findSessionById(serverSessions, sessionId)" in body, name
        assert "serverSessions.find(" not in body and "serverSessions.some(" not in body, name


def _keyframes(css: str, name: str) -> str:
    m = re.search(r"@keyframes\s+" + re.escape(name) + r"\s*\{", css)
    assert m, name
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
    return css[m.end():i]


@pytest.mark.parametrize("name", ["typing-wave-bar", "history-running-glow"])
def test_working_indicators_animate_compositor_properties_only(name):
    body = _keyframes(CHAT_CSS.read_text(encoding="utf-8"), name)
    props = set(re.findall(r"([a-z-]+)\s*:", body))
    assert props <= {"transform", "opacity"}, (name, props)
