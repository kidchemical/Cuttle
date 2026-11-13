"""Fleet-card peek drawer: frame URL and header from the clicked card (pure helpers)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PEEK_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat" / "chat_subagent_peek.js"
CHAT_PAGE = PEEK_JS.parent / "chat_page.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_peek_layout_setting_defaults_to_side():
    code = r"""
const P = require(process.env.PEEK_JS);
console.log(JSON.stringify([P.layoutFor('center'), P.layoutFor('side'), P.layoutFor(null), P.layoutFor('x')]));
"""
    out = json.loads(subprocess.run(["node", "-e", code], env={**os.environ, "PEEK_JS": str(PEEK_JS)},
                                    capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert out == ["center", "side", "side", "side"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_peek_src_and_header():
    code = r"""
const P = require(process.env.PEEK_JS);
const h = {display:'CH-000042', sessionId:'42'};
const m = {display:'CH-000042-7', sessionId:'42', messageIndex:7};
console.log(JSON.stringify({
  src: P.peekSrc(h), msg: P.peekSrc(m), none: P.peekSrc(null),
  named: P.headerModel(h, {title:' Scout ', status:'Done'}),
  bare: P.headerModel(h, {}),
}));
"""
    out = json.loads(subprocess.run(["node", "-e", code], env={**os.environ, "PEEK_JS": str(PEEK_JS)},
                                    capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    # peek=1 keeps the nested page from opening drawers of its own.
    assert out["src"] == "/chat_page.html?chat=42&peek=1"
    assert out["msg"] == "/chat_page.html?chat=42&peek=1&msg=7"
    assert out["none"] == ""
    assert out["named"] == {"title": "Scout", "handle": "CH-000042", "status": "Done"}
    assert out["bare"] == {"title": "CH-000042", "handle": "", "status": ""}


def test_fleet_card_click_opens_peek_and_plain_launchers_still_navigate():
    src = CHAT_PAGE.read_text(encoding="utf-8")
    assert "launcher.classList.contains('subagent-fleet-card') && subagentPeek()" in src
    assert "has('peek')" in src  # no nested drawers inside a peek frame
