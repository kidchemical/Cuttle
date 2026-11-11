"""Session menu items (src/web/js/chat/chat_session_menu.js): pure markup under node.

The header dropdown lifecycle is exercised by the browser suites that open
#chatChromeMenuBtn (supervision, composer lock).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "chat" / "chat_session_menu.js"

HARNESS = r"""
const M = require(process.env.MOD);
const icons = new Proxy({}, { get: (_, k) => '<i:' + String(k) + '>' });
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const full = M.itemsHtml({ sessionId: "4'2", displayId: 'CH-000042', starred: true, includeRename: true,
  includeDelete: true, subagents: { collapsed: true }, extraHtml: ['<b>git</b>', '', '<b>lock</b>'],
  archive: { archived: false } }, icons, esc);
const bare = M.itemsHtml({ sessionId: null, extraHtml: [] }, icons, esc);
console.log(JSON.stringify({ full, bare }));
"""


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "MOD": str(MOD)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _labels(html):
    return re.findall(r'chat-chrome-menu-item-label">([^<]*)<', html)


def test_item_order_and_owner_items():
    r = _run()
    assert _labels(r["full"]) == ["Copy CH-000042", "Export", "Unstar chat", "Mark as unread", "Rename",
                                  "Show sub-agents", "Voice", "Archive", "Delete"]
    full = r["full"]
    assert full.index("<b>git</b>") < full.index("<b>lock</b>") < full.index(">Voice<")
    assert "is-starred" in full and "is-danger" in full
    # Session ids are escaped for the inline JS string argument.
    assert "window.chatPageExportSession('4\\'2', event)" in full


def test_no_session_offers_only_voice():
    r = _run()
    assert _labels(r["bare"]) == ["Voice"]
