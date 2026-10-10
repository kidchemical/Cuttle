"""Composer prompt-enhance (wand) behavioral characterization.

Exercises the REAL ``enhanceComposerPrompt`` from ``chat_page.js`` under Node
with a stubbed textarea / button / fetch, pinning the click decision
(single-flight, revert, empty guard), the request body, and the response
decision (apply / no-change / error). Phase 2 slice 2D moved the pure
decisions into ``chat_composer.js``; the page keeps the DOM/fetch adapter.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
COMPOSER_JS = CHAT_JS.parent / "chat_composer.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = r"""
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const span = (s, e) => SRC.slice(SRC.indexOf(s), SRC.indexOf(e, SRC.indexOf(s)));
const CuttleChatComposer = require(process.env.MOD_JS);

const toasts = [];
const window = { showToast: (m, t) => toasts.push([String(m), String(t)]) };
const document = { getElementById: () => null, querySelectorAll: () => [] };
const CHAT_RECORD_SELECTOR = '.chat-record';
const currentProject = null;
function autoResizeTextarea() {}
function autoResizeWelcomeTextarea() {}
let fetchCalls = 0;
let fetchImpl = async () => { throw new Error('no fetch'); };
function fetch(...a) { fetchCalls++; return fetchImpl(...a); }

eval(span("    const enhanceState = ",
          "    function wireEnhanceControls() {"));

function makeBtn() {
  const s = new Set();
  return { title: '', disabled: false,
    classList: { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c) } };
}
function makeTa(v) { return { value: v, focus() {}, setSelectionRange() {} }; }

const jsonResp = (status, body) => ({ ok: status >= 200 && status < 300, status,
  json: async () => body });

async function main() {
  const scenario = process.env.SCENARIO;
  const out = { toasts, fetchCalls: 0 };
  if (scenario === 'applyThenRevert') {
    const ta = makeTa('rough ask');
    const btn = makeBtn();
    fetchImpl = async () => jsonResp(200, { success: true, prompt: 'Polished ask', source: 'openai' });
    await enhanceComposerPrompt('chat', btn, ta);
    out.afterApply = { value: ta.value, enhanced: btn.classList.contains('is-enhanced'),
      busy: btn.classList.contains('is-busy'), disabled: btn.disabled };
    await enhanceComposerPrompt('chat', btn, ta);
    out.afterRevert = { value: ta.value, enhanced: btn.classList.contains('is-enhanced') };
  } else if (scenario === 'empty') {
    const ta = makeTa('   ');
    const btn = makeBtn();
    await enhanceComposerPrompt('chat', btn, ta);
    out.value = ta.value;
    out.enhanced = btn.classList.contains('is-enhanced');
  } else if (scenario === 'noChange') {
    const ta = makeTa('already clear');
    const btn = makeBtn();
    fetchImpl = async () => jsonResp(200, { success: true, prompt: 'already clear' });
    await enhanceComposerPrompt('chat', btn, ta);
    out.value = ta.value;
    out.enhanced = btn.classList.contains('is-enhanced');
  } else if (scenario === 'unchangedFlag') {
    const ta = makeTa('already clear');
    const btn = makeBtn();
    fetchImpl = async () => jsonResp(200, { success: true, prompt: 'Reworded', unchanged: true });
    await enhanceComposerPrompt('chat', btn, ta);
    out.value = ta.value;
  } else if (scenario === 'error') {
    const ta = makeTa('rough');
    const btn = makeBtn();
    fetchImpl = async () => jsonResp(500, null);
    await enhanceComposerPrompt('chat', btn, ta);
    out.value = ta.value;
    out.busy = btn.classList.contains('is-busy');
    out.disabled = btn.disabled;
  } else if (scenario === 'singleFlight') {
    const ta = makeTa('rough');
    const btn = makeBtn();
    let release;
    fetchImpl = () => new Promise((res) => { release = () => res(jsonResp(200, { success: true, prompt: 'Polished' })); });
    const first = enhanceComposerPrompt('chat', btn, ta);
    await enhanceComposerPrompt('chat', btn, ta); // busy → returns immediately
    release();
    await first;
    out.value = ta.value;
  }
  out.fetchCalls = fetchCalls;
  process.stdout.write(JSON.stringify(out));
}
main().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run(scenario: str):
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={
            **os.environ,
            "CHAT_PAGE_JS": str(CHAT_JS),
            "MOD_JS": str(COMPOSER_JS),
            "SCENARIO": scenario,
        },
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_enhance_applies_then_reverts():
    res = _run("applyThenRevert")
    assert res["afterApply"] == {
        "value": "Polished ask", "enhanced": True, "busy": False, "disabled": False,
    }
    assert res["afterRevert"] == {"value": "rough ask", "enhanced": False}
    assert res["fetchCalls"] == 1
    assert res["toasts"][0][0].startswith("Prompt enhanced (openai)")
    assert res["toasts"][0][1] == "success"
    assert res["toasts"][1] == ["Reverted to your original prompt", "info"]


@node_only
def test_enhance_empty_guard_never_fetches():
    res = _run("empty")
    assert res["fetchCalls"] == 0
    assert res["toasts"] == [["Type a prompt first", "info"]]
    assert res["enhanced"] is False


@node_only
def test_enhance_no_change_toast():
    res = _run("noChange")
    assert res["fetchCalls"] == 1
    assert res["toasts"] == [["Prompt already looks clear", "info"]]
    assert res["value"] == "already clear"
    assert res["enhanced"] is False


@node_only
def test_enhance_unchanged_flag_toast():
    res = _run("unchangedFlag")
    assert res["toasts"] == [["Prompt already looks clear", "info"]]
    assert res["value"] == "already clear"


@node_only
def test_enhance_http_error_toast_and_cleanup():
    res = _run("error")
    assert res["toasts"][0][0] == "Enhance failed (HTTP 500)"
    assert res["toasts"][0][1] == "error"
    assert res["value"] == "rough"
    assert res["busy"] is False and res["disabled"] is False


@node_only
def test_enhance_is_single_flight():
    res = _run("singleFlight")
    assert res["fetchCalls"] == 1
    assert res["value"] == "Polished"