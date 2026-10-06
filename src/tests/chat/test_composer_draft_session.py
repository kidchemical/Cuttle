"""Composer drafts must stay pinned to the chat they were typed in.

Repro: type into chat A → switch to chat B → leftover text still sits in the
prompt. Opening New Chat must show an empty composer (unless that splash has
its own saved ``new`` draft).

Exercises the real draft helpers from ``chat_page.js`` under Node. Soft-restore
must not keep a prior session's textarea value when the destination draft is
empty — that is the leak.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"


def _extract_range(src: str, start: str, end: str) -> str:
    head = src.index(start)
    tail = src.index(end, head)
    return src[head:tail]


_HARNESS = r"""
const assert = require('assert');

const store = Object.create(null);
const localStorage = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
  setItem(k, v) { store[k] = String(v); },
  removeItem(k) { delete store[k]; },
  clear() { for (const k of Object.keys(store)) delete store[k]; },
};

function makeEl(id) {
  return { id, value: '', style: { display: 'none' } };
}
const welcomeScreen = makeEl('welcomeScreen');
const welcomeChatInput = makeEl('welcomeChatInput');
const chatInput = makeEl('chatInput');
welcomeScreen.style.display = 'none';

const document = {
  getElementById(id) {
    if (id === 'welcomeScreen') return welcomeScreen;
    if (id === 'welcomeChatInput') return welcomeChatInput;
    if (id === 'chatInput') return chatInput;
    return null;
  },
};

let currentSessionId = null;
const newComposerDraftId = 'new';
const newComposerPrefsId = 'draft:new';
const prefs = {};
function getSessionPrefs(id) { return prefs[id] || null; }
function updateSessionPrefs(id, patch) { prefs[id] = {...prefs[id], ...patch}; }
const slashCtx = {chat: {chips: []}, welcome: {chips: []}};
const slashPaletteSupplement = {};
function renderSlashChips() {}
const CuttleChatComposer = {draftChips: chips => chips.slice()};
const CuttleChatAgentModel = {draftOverrides: () => ({}), draftSupplementPatch: () => ({})};
function autoResizeTextarea() {}
function autoResizeWelcomeTextarea() {}
function syncSlashMenuFromInput() {}

__HELPERS__

function showChatComposer() {
  welcomeScreen.style.display = 'none';
}
function showWelcomeComposer() {
  welcomeScreen.style.display = 'flex';
}

// Mirrors loadChatSession / createNewChat after the force-restore fix.
function switchToSession(fromId, toId) {
  if (fromId != null) saveComposerDraft(fromId);
  else saveComposerDraft('new');
  currentSessionId = toId;
  showChatComposer();
  restoreComposerDraft(toId, { force: true });
}

function openNewChat(fromId) {
  if (fromId != null) saveComposerDraft(fromId);
  currentSessionId = null;
  showWelcomeComposer();
  restoreComposerDraft('new', { force: true });
}

const cases = __CASES__;
for (const c of cases) {
  localStorage.clear();
  welcomeChatInput.value = '';
  chatInput.value = '';
  welcomeScreen.style.display = 'none';
  currentSessionId = null;
  c.run({
    assert,
    localStorage,
    chatInput,
    welcomeChatInput,
    welcomeScreen,
    setSession(id) { currentSessionId = id; },
    getSession() { return currentSessionId; },
    showChatComposer,
    showWelcomeComposer,
    switchToSession,
    openNewChat,
    saveComposerDraft,
    restoreComposerDraft,
    clearComposerDraft,
    _draftKey,
  });
}
console.log('composer-draft-session-ok');
"""

_DRIVER_CASES = r"""
[
  {
    name: 'switch-clears-empty-destination',
    run(h) {
      h.setSession('101');
      h.showChatComposer();
      h.chatInput.value = 'draft for chat A';
      h.saveComposerDraft('101');
      h.switchToSession('101', '202');
      h.assert.strictEqual(h.chatInput.value, '',
        'destination with no draft must clear leftover composer text');
      h.assert.strictEqual(h.localStorage.getItem(h._draftKey('101')), 'draft for chat A',
        'leaving chat must keep its draft pinned');
    },
  },
  {
    name: 'return-restores-pinned-draft',
    run(h) {
      h.setSession('101');
      h.showChatComposer();
      h.chatInput.value = 'draft for chat A';
      h.switchToSession('101', '202');
      h.chatInput.value = 'typing in B';
      h.switchToSession('202', '101');
      h.assert.strictEqual(h.chatInput.value, 'draft for chat A',
        'returning to A must restore As pinned draft');
      h.switchToSession('101', '202');
      h.assert.strictEqual(h.chatInput.value, 'typing in B',
        'returning to B must restore Bs pinned draft');
    },
  },
  {
    name: 'new-chat-clears-composer',
    run(h) {
      h.setSession('101');
      h.showChatComposer();
      h.chatInput.value = 'unsent in A';
      h.openNewChat('101');
      h.assert.strictEqual(h.welcomeChatInput.value, '',
        'new chat splash must not show prior session text');
      h.assert.strictEqual(h.chatInput.value, '',
        'hidden chat composer must also clear so it cannot leak later');
      h.assert.strictEqual(h.localStorage.getItem(h._draftKey('101')), 'unsent in A',
        'prior chat draft stays pinned under its session id');
    },
  },
  {
    name: 'new-chat-restores-own-draft-only',
    run(h) {
      h.setSession(null);
      h.showWelcomeComposer();
      h.welcomeChatInput.value = 'splash draft';
      h.saveComposerDraft('new');
      h.setSession('101');
      h.showChatComposer();
      h.chatInput.value = 'session text';
      h.openNewChat('101');
      h.assert.strictEqual(h.welcomeChatInput.value, 'splash draft',
        'new chat may restore the splashs own draft');
    },
  },
]
"""


def _helpers_src(src: str) -> str:
    return _extract_range(
        src,
        "    const COMPOSER_DRAFT_MAX = 50000;",
        "    /**\n     * True while this chat has an in-flight reply",
    )


def _fn_chunk(src: str, name: str, nbytes: int = 20000) -> str:
    idx = src.index(name)
    return src[idx : idx + nbytes]


@node_only
def test_switch_to_chat_without_draft_clears_composer(tmp_path):
    """Chat A typed text must not leak into chat B when B has no draft."""
    src = CHAT_JS.read_text(encoding="utf-8")
    helpers = _helpers_src(src)
    harness = (
        _HARNESS.replace("__HELPERS__", helpers).replace("__CASES__", _DRIVER_CASES)
    )
    driver = tmp_path / "composer-draft-session.js"
    driver.write_text(harness, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
    assert "composer-draft-session-ok" in proc.stdout


@node_only
def test_load_and_new_chat_force_restore_destination_draft():
    """Session navigation must force-apply the destination draft (incl. empty).

    Soft restore's "don't clobber typed text" guard is for boot races only —
    switch / new-chat paths must pass force so an empty destination clears
    the textarea.
    """
    src = CHAT_JS.read_text(encoding="utf-8")
    load_chunk = _fn_chunk(src, "async function loadChatSession", 25000)
    create_chunk = _fn_chunk(src, "async function createNewChat", 4000)
    splash_chunk = _fn_chunk(src, "function showWelcomeSplash", 1200)

    assert "restoreComposerDraft(" in load_chunk
    assert "restoreComposerDraft(" in create_chunk

    def _has_force_restore(chunk: str, sid_expr: str) -> bool:
        needles = (
            f"restoreComposerDraft({sid_expr}, {{ force: true }})",
            f"restoreComposerDraft({sid_expr}, {{force: true}})",
            f"restoreComposerDraft({sid_expr},{{force:true}})",
            f"restoreComposerDraft({sid_expr}, {{ force:true }})",
        )
        return any(n in chunk for n in needles)

    assert _has_force_restore(load_chunk, "sessionId"), (
        "loadChatSession must restoreComposerDraft(sessionId, { force: true }) "
        "so empty destination drafts clear leftover textarea text"
    )
    assert _has_force_restore(create_chunk, "'new'"), (
        "createNewChat must restoreComposerDraft('new', { force: true }) "
        "so New Chat clears leftover composer text"
    )
    assert _has_force_restore(splash_chunk, "'new'"), (
        "showWelcomeSplash must restoreComposerDraft('new', { force: true })"
    )

    restore_start = src.index("function restoreComposerDraft(sid,")
    restore_body = src[restore_start : restore_start + 3000]
    assert "force" in restore_body
    # Empty destination must clear the active field when forced.
    assert "el.value = text" in restore_body or 'el.value = text' in restore_body
