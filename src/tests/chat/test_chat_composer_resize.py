"""Welcome composer resize math (src/web/js/chat/chat_composer_resize.js) under node."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RESIZE_JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "chat" / "chat_composer_resize.js"

HARNESS = r"""
const R = require(process.env.RESIZE_JS);
console.log(JSON.stringify({
  autoSmall: R.fitHeight({scrollHeight: 50}),
  autoCapped: R.fitHeight({scrollHeight: 400}),
  userTall: R.fitHeight({scrollHeight: 50, userHeight: 300, max: 600}),
  userOverflow: R.fitHeight({scrollHeight: 900, userHeight: 300, max: 600}),
  userShort: R.fitHeight({scrollHeight: 120, userHeight: 60, max: 600}),
  userClamped: R.fitHeight({scrollHeight: 50, userHeight: 5000, max: 600}),
  clampMin: R.clampHeight(3, 600),
  parsed: [R.parseStored('250'), R.parseStored('x'), R.parseStored('10'), R.parseStored(null)],
}));
"""


def test_fit_height_combines_auto_grow_and_dragged_height():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "RESIZE_JS": str(RESIZE_JS)})
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["autoSmall"] == {"height": 50, "overflow": False}
    assert r["autoCapped"] == {"height": 168, "overflow": True}
    assert r["userTall"] == {"height": 300, "overflow": False}
    assert r["userOverflow"] == {"height": 300, "overflow": True}
    assert r["userShort"] == {"height": 120, "overflow": False}  # still auto-grows past a short drag
    assert r["userClamped"] == {"height": 600, "overflow": False}
    assert r["clampMin"] == 44
    assert r["parsed"] == [250, None, None, None]
