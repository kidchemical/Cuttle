"""Post-paint activation frontend domain.

Behavioral characterization of src/web/js/chat_activate.js under node
(Phase 3 Slice 8D). The module owns container-scoped DOM activation
for rendered assistant content: Vega chart embeds and code-copy
buttons. Render planning (placeholder HTML) stays in
`CuttleChatMessages`; orchestration (`activateEnhancements`: hljs,
terminal/button execution wiring) stays in the page, which calls in
here with explicit dependencies.

Every DOM surface is a fake in these tests; the assertions pin call
order, idempotence, error/skip paths, and container scoping.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat_activate.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
globalThis.CuttleChatMessages = require(process.env.MOD_MESSAGES_JS);
const A = require(process.env.MOD_JS);
const out = {};
const mkEl = () => {
  const listeners = {};
  const el = { _cls: new Set(), _kids: [], _attrs: {}, _text: '', _html: '',
    parentElement: null, __vega_done: false,
    classList: { add(c) { el._cls.add(c); }, remove(c) { el._cls.delete(c); },
      toggle(c, f) { if (f) el._cls.add(c); else el._cls.delete(c); },
      contains(c) { return el._cls.has(c); } },
    setAttribute(k, v) { el._attrs[k] = v; },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(el._attrs, k) ? el._attrs[k] : null; },
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    appendChild(k) { el._kids.push(k); k.parentElement = el; return k; },
    querySelector(sel) {
      return el._kids.find((k) => k._cls && k._cls.has('code-copy-btn')) || null;
    },
  };
  Object.defineProperty(el, 'textContent', { set(v) { el._text = v; }, get() { return el._text; } });
  Object.defineProperty(el, 'className', {
    set(v) { String(v).split(/\s+/).forEach((c) => { if (c) el._cls.add(c); }); },
    get() { return [...el._cls].join(' '); } });
  Object.defineProperty(el, 'innerHTML', { set(v) { el._html = v; }, get() { return el._html; } });
  el._listeners = listeners;
  return el;
};
// --- vega activation on a fake container
const embedCalls = [];
const vegaDeps = { vegaEmbed: async (el, spec, opts) => { embedCalls.push({ spec, opts }); } };
const good = mkEl(); good.setAttribute('data-vega-spec', '{"mark": "point"}');
const bad = mkEl(); bad.setAttribute('data-vega-spec', '{oops');
const done = mkEl(); done.setAttribute('data-vega-spec', '{"a": 1}'); done.__vega_done = true;
const seenSelectors = [];
const containerA = { querySelectorAll(sel) { seenSelectors.push(sel); return [good, bad, done]; } };
const other = mkEl(); other.setAttribute('data-vega-spec', '{"z": 9}');
A.activateVegaEmbeds(containerA, vegaDeps);
out.vega = { embedCalls: embedCalls.map((c) => c.spec),
  embedBg: embedCalls.length ? embedCalls[0].opts : null,
  goodDone: good.__vega_done, badError: bad._cls.has('vega-wrap--error'),
  badText: bad._text, otherDone: other.__vega_done,
  selector: seenSelectors[0] };
A.activateVegaEmbeds(containerA, {});
out.vegaNoLib = { embedCalls: embedCalls.length };
// --- code-copy activation
const created = [];
const copied = [];
const toasted = [];
let laterCb = null; let laterMs = null;
const copyDeps = { createElement: (t) => { const b = mkEl(); b.tag = t; created.push(b); return b; },
  copyIcon: '<svg>COPY</svg>', copiedIcon: '<svg>OK</svg>',
  copyText: async (t) => { copied.push(t); return copyDeps._ok; },
  notifyError: (m) => { toasted.push(m); },
  later: (fn, ms) => { laterCb = fn; laterMs = ms; } };
copyDeps._ok = true;
const pre = mkEl();
const code = mkEl(); code._text = 'a\\u200Bb\\u00A0c\\n\\n'; code.parentElement = pre;
const bare = mkEl();
const scopedOut = mkEl();
const containerB = { querySelectorAll(sel) { return sel === 'pre > code' ? [code, bare] : []; } };
A.attachCodeCopyButtons(containerB, copyDeps);
const btns = created.filter((b) => b.tag === 'button');
out.copy = { buttons: btns.length, btnClass: btns.length ? [...btns[0]._cls][0] : null,
  btnIcon: btns.length ? btns[0]._html : null, preMarked: pre._cls.has('has-code-copy'),
  bareKids: bare._kids.length, scopedKids: scopedOut._kids.length };
A.attachCodeCopyButtons(containerB, copyDeps);
out.copyIdempotent = { buttons: created.filter((b) => b.tag === 'button').length };
const clickEv = { preventDefault() {}, stopPropagation() {} };
await btns[0]._listeners['click'][0](clickEv);
out.copyClick = { copied: [...copied], btnTitle: btns[0].title, swapped: btns[0]._html, laterMs };
await laterCb();
out.copyRearm = { icon: btns[0]._html, title: btns[0].title, marked: btns[0]._cls.has('is-copied') };
copyDeps._ok = false;
await btns[0]._listeners['click'][0](clickEv);
out.copyFail = { toasted, failTitle: btns[0].title };
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


CHAT_MESSAGES_JS = REPO_ROOT / "src" / "web" / "js" / "chat_messages.js"


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "MOD_MESSAGES_JS": str(CHAT_MESSAGES_JS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_vega_activation_embed_error_skip():
    res = _run()["vega"]
    assert len(res["embedCalls"]) == 1
    assert res["embedCalls"][0]["mark"] == "point"
    assert res["embedCalls"][0]["background"] is None
    assert res["embedCalls"][0]["config"] == {"background": None, "view": {"stroke": None}}
    # transparent-background patches for flush-in-bubble charts
    assert res["embedBg"]["config"]["background"] is None
    assert res["embedBg"]["renderer"] == "svg"
    assert res["goodDone"] is True
    assert res["badError"] is True
    assert "Invalid Vega JSON" in res["badText"]
    # container scoping: only the passed container is queried (done-ness is
    # tracked per element via __vega_done, not the selector); other mounts idle
    assert res["selector"] == "[data-vega-spec]"
    assert res["otherDone"] is False


@node_only
def test_vega_activation_without_library_is_noop():
    res = _run()["vegaNoLib"]
    assert res["embedCalls"] == 1


@node_only
def test_code_copy_button_lifecycle():
    res = _run()
    assert res["copy"]["buttons"] == 1
    assert res["copy"]["btnClass"] == "code-copy-btn"
    assert res["copy"]["btnIcon"] == "<svg>COPY</svg>"
    assert res["copy"]["preMarked"] is True
    # bare code element (no pre parent wiring point) gets nothing; other mounts idle
    assert res["copy"]["bareKids"] == 0
    assert res["copy"]["scopedKids"] == 0
    # idempotent: second pass adds no duplicate button
    assert res["copyIdempotent"]["buttons"] == 1
    # click copies cleaned text (zero-width dropped, nbsp flattened)
    assert res["copyClick"]["copied"] == ["ab c"]
    assert res["copyClick"]["btnTitle"] == "Copied!"
    assert res["copyClick"]["swapped"] == "<svg>OK</svg>"
    assert res["copyClick"]["laterMs"] == 1500
    # timer re-arms the button
    assert res["copyRearm"] == {"icon": "<svg>COPY</svg>", "title": "Copy code", "marked": False}
    # clipboard failure toasts instead of marking
    assert res["copyFail"]["toasted"] == ["Could not copy code block"]
    assert res["copyFail"]["failTitle"] == "Copy failed"


@node_only
def test_chat_activate_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_chat_activate_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(encoding="utf-8")
    tag = '<script src="/js/chat_activate.js?v='
    assert tag in html, "chat_activate.js must load via versioned script tag"
    assert html.index("chat_messages.js") < html.index("chat_activate.js") < html.index(
        "chat_page.js?v="
    ), "load order: owned modules before the page orchestrator"
