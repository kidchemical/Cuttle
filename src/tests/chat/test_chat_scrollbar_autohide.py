"""Auto-hiding chat scrollbars (src/web/js/chat/chat_scrollbar_autohide.js) under node."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "chat" / "chat_scrollbar_autohide.js"

HARNESS = r"""
global.window = { setTimeout: (fn) => { pending.push(fn); return pending.length; } };
const pending = [];
require(process.env.AUTOHIDE_JS);
const A = window.CuttleChatScrollbarAutohide;
function fakeEl() {
  const cls = new Set(), handlers = {};
  return {
    classList: { add: (...c) => c.forEach(x => cls.add(x)), remove: (...c) => c.forEach(x => cls.delete(x)),
                 contains: (c) => cls.has(c) },
    addEventListener: (t, fn) => { handlers[t] = fn; }, removeEventListener: (t) => { delete handlers[t]; },
    matches: () => false, scrollHeight: 1000, clientHeight: 400,
    getBoundingClientRect: () => ({ right: 500 }),
    fire: (t, ev) => handlers[t] && handlers[t](ev || {}), cls,
  };
}
const el = fakeEl();
const ctl = A.create({ doc: { querySelectorAll: (s) => s === '#chatMessages' ? [el] : [] }, win: window });
const r = { base: el.cls.has('scrollbar-autohide'), idle: el.cls.has('is-scrolling') };
el.fire('scroll'); r.scrolling = el.cls.has('is-scrolling');
pending.splice(0).forEach(fn => fn()); r.afterIdle = el.cls.has('is-scrolling');
el.fire('pointermove', { clientX: 300 }); r.farMove = el.cls.has('is-scrolling');
el.fire('pointermove', { clientX: 495 }); r.edgeMove = el.cls.has('is-scrolling');
ctl.destroy(); r.destroyed = !el.cls.has('scrollbar-autohide') && !el.cls.has('is-scrolling');
console.log(JSON.stringify(r));
"""


def test_scrollbar_shows_while_scrolling_and_hides_when_idle():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "AUTOHIDE_JS": str(JS)})
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {
        "base": True, "idle": False, "scrolling": True, "afterIdle": False,
        "farMove": False, "edgeMove": True, "destroyed": True,
    }


def test_chat_css_hides_thumb_until_scrolling():
    css = (JS.parents[2] / "css" / "chat_page.css").read_text(encoding="utf-8")
    assert ".scrollbar-autohide {" in css
    assert ".scrollbar-autohide.is-scrolling {" in css
