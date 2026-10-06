"""Working-bubble agent badge must survive leave + return mid-run.

Repro that these tests guard:
1. Send with a sticky agent badge (e.g. /cursor) → typing / "Working…"
   bubble header shows the agent chip immediately.
2. Switch to another chat, then come back while the run is still active.
3. The remote-waiting bubble must still show the agent badge.

Fix (chat_page.js):
- ``restoreSessionStickySlash`` runs before ``updateRemoteWaitingFromMessages``
  so ``currentTypingSlashMeta()`` sees this session's sticky chips.
- Reopen typing meta covers all sticky agents (not Cursor-only).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
SLASH_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_slash.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


def _load_chat_session_body(src: str) -> str:
    """Slice of loadChatSession that loads messages + live-status + sticky."""
    # Anchor on the transcript paint + live-status reconcile block.
    start = src.index("updateRemoteWaitingFromMessages(loadedMessages, liveStatus)")
    # Walk backward to the messages-success branch for context.
    branch = src.rfind("if (data.success && Array.isArray(data.messages))", 0, start)
    end = src.index("restoreComposerDraft(sessionId, { force: true })", start)
    return src[branch:end]


def test_load_chat_session_restores_sticky_before_remote_waiting_bubble():
    """Sticky chips must be restored before the mid-run working bubble is painted.

    Otherwise ``currentTypingSlashMeta()`` sees no agent chip and the
    reopen path bakes a badge-less typing indicator.
    """
    src = CHAT_JS.read_text(encoding="utf-8")
    body = _load_chat_session_body(src)
    sticky_at = body.index("restoreSessionStickySlash(sessionId, loadedMessages)")
    waiting_at = body.index("updateRemoteWaitingFromMessages(loadedMessages, liveStatus)")
    assert sticky_at < waiting_at, (
        "restoreSessionStickySlash must run before updateRemoteWaitingFromMessages "
        "so the working bubble can read the session's agent badge on reopen. "
        f"Today sticky is at offset {sticky_at}, remote-waiting at {waiting_at}."
    )


def test_load_chat_session_refreshes_agent_badge_after_sticky_if_bubble_already_painted():
    """Hub/phone can paint the working bubble before sticky restore finishes.

    ``restoreSessionStickySlash`` must refresh header badges (agent + project)
    so an already-mounted typing indicator picks up the agent chip.
    """
    src = CHAT_JS.read_text(encoding="utf-8")
    restore_fn = _extract(
        src,
        "    function restoreSessionStickySlash(sessionId, messages) {",
        "    function persistProjectForCurrentSession(opts)",
    )
    assert "refreshTypingIndicatorHeaderBadges" in restore_fn, (
        "restoreSessionStickySlash must refresh typing-indicator header badges "
        "so a hub-painted working bubble gains the agent chip after sticky lands."
    )
    refresh_fn = _extract(
        src,
        "    function refreshTypingIndicatorHeaderBadges() {",
        "    /**\n     * Keep the in-memory auth session + last /messages hydrate in sync with the",
    )
    assert "currentTypingSlashMeta" in refresh_fn
    assert "messageHeaderBadgesHtml" in refresh_fn
    # Prefs-only restore before shell live-status (phone hub race).
    assert "restoreSessionStickySlash(sessionId, [])" in src
    early = src.index("restoreSessionStickySlash(sessionId, [])")
    shell = src.index("reportSessionToShell(sessionId, { preferFocus: true })")
    assert early < shell, (
        "prefs sticky restore must run before reportSessionToShell so hub "
        "live-status paint sees this chat's agent chip"
    )


@node_only
def test_hub_early_paint_then_sticky_restore_adds_agent_badge():
    """Simulate phone hub: badge-less meta first, then sticky restore recovers it."""
    src = CHAT_JS.read_text(encoding="utf-8")
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    commands = _extract(slash_src, "const SLASH_COMMANDS = [", "\n];") + "\n];"
    chip_preds = _extract(
        src,
        "    function isStickyAgentChip(chip, agentId) {",
        "    /** Nested Cursor Agent slash",
    )
    has_active = _extract(
        src,
        "    /** True when the Cursor Agent sticky badge/chip is applied to this composer. */",
        "    function hasActiveMuseAgentChip(key) {",
    )
    active_fn = _extract(
        src,
        "    /**\n     * Active sticky agent composer chip (Cursor / Muse / Codex",
        "    /**\n     * Sticky agent chip + preferred model/effort — used when typing UI is",
    )
    meta_fn = _extract(
        src,
        "    /**\n     * Sticky agent chip + preferred model/effort — used when typing UI is",
        "    function syncPreferredModelFromResponse(data) {",
    )
    pending = _extract(
        src,
        "    /**\n     * Derive the chip(s) to show in the typing indicator",
        "    /**\n     * Active sticky agent composer chip",
    )
    slash_mod = str(SLASH_JS)
    lean = f"""
const CuttleChatSlash = require("{slash_mod}");
{commands}
const prefsMap = {{
  'CH-000503': {{
    stickyChips: [{{ prefix: '/cursor ', label: 'Cursor Agent', category: 'command' }}],
  }},
}};
let currentSessionId = 'CH-000503';
let stickyAgentClearedPending = false;
const slashCtx = {{ chat: {{ chips: [] }}, welcome: {{ chips: [] }} }};
const slashPaletteSupplement = {{ preferredModel: 'auto', cursorModels: [] }};
function getSessionPrefs(id) {{ return prefsMap[String(id)] || null; }}
function updateSessionPrefs() {{}}
function renderSlashChips() {{}}
function refreshTypingIndicatorHeaderBadges() {{}}
const document = {{ getElementById: () => null }};
function enrichSlashCommandWithMuseModel(s) {{ return s; }}
function enrichSlashCommandWithHermesModel(s) {{ return s; }}
function enrichSlashCommandWithOpenCodeModel(s) {{ return s; }}
function enrichSlashCommandWithCodexModel(s) {{ return s; }}
function enrichSlashCommandWithCursorRun() {{
  return {{ chips: [{{ label: 'Cursor - Auto', meta: '/cursor', category: 'cursor' }}] }};
}}
function isCursorRelatedSlashChip() {{ return true; }}
{chip_preds}
{has_active}
{active_fn}
{pending}
{meta_fn}
function restoreSessionStickySlash(sessionId) {{
  const prefs = getSessionPrefs(sessionId);
  let sticky = prefs && Array.isArray(prefs.stickyChips) ? prefs.stickyChips.slice() : [];
  slashCtx.welcome.chips = sticky.slice();
  slashCtx.chat.chips = sticky.slice();
}}
const earlyMeta = currentTypingSlashMeta();
restoreSessionStickySlash('CH-000503');
const lateMeta = currentTypingSlashMeta();
process.stdout.write(JSON.stringify({{
  early: earlyMeta,
  lateLabels: (lateMeta && lateMeta.chips || []).map((c) => c.label),
}}));
"""
    proc = subprocess.run(
        ["node", "-e", lean], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    res = json.loads(proc.stdout)
    assert res["early"] is None, res
    assert any("Cursor" in str(lab) for lab in res["lateLabels"]), res



@node_only
def test_reopen_working_bubble_meta_requires_restored_sticky_chips():
    """Behavioral: reopen must see sticky chips when the working bubble is painted.

    Runs the same helpers ``loadChatSession`` uses, in the same relative order
    as the source. Today sticky restore follows remote-waiting paint, so the
    Cursor badge gate is still false when the bubble would be created.
    """
    src = CHAT_JS.read_text(encoding="utf-8")
    body = _load_chat_session_body(src)
    sticky_before_waiting = (
        body.index("restoreSessionStickySlash(sessionId, loadedMessages)")
        < body.index("updateRemoteWaitingFromMessages(loadedMessages, liveStatus)")
    )

    slash_src = SLASH_JS.read_text(encoding="utf-8")
    commands = _extract(slash_src, "const SLASH_COMMANDS = [", "\n];") + "\n];"
    sticky_helpers = _extract(
        src,
        "    function getStickySlashCommandFromMessage(message) {",
        "    function persistProjectForCurrentSession(opts)",
    )
    chip_preds = _extract(
        src,
        "    function isStickyAgentChip(chip, agentId) {",
        "    /** Nested Cursor Agent slash",
    )
    has_active = _extract(
        src,
        "    /** True when the Cursor Agent sticky badge/chip is applied to this composer. */",
        "    function hasActiveMuseAgentChip(key) {",
    )
    meta_src = _extract(
        src,
        "    /**\n     * Sticky agent chip + preferred model/effort — used when typing UI is",
        "    function syncPreferredModelFromResponse(data) {",
    )
    assert "activeStickyAgentChip()" in meta_src
    assert "return null" in meta_src
    active_fn = _extract(
        src,
        "    /**\n     * Active sticky agent composer chip (Cursor / Muse / Codex",
        "    /**\n     * Sticky agent chip + preferred model/effort — used when typing UI is",
    )
    assert "CuttleChatSlash.activeStickyAgentChip" in active_fn

    slash_mod = str(SLASH_JS)
    harness = f"""
const CuttleChatSlash = require("{slash_mod}");
{commands}
const prefsMap = {{}};
let currentSessionId = 'CH-000503';
const slashCtx = {{
    chat: {{ chips: [] }},
    welcome: {{ chips: [] }},
}};
function getSessionPrefs(id) {{
    if (id == null || id === '') return null;
    return prefsMap[String(id)] || null;
}}
function updateSessionPrefs(id, patch) {{
    if (id == null || id === '') return;
    prefsMap[String(id)] = Object.assign({{}}, prefsMap[String(id)] || {{}}, patch || {{}});
}}
function renderSlashChips() {{}}
function refreshTypingIndicatorHeaderBadges() {{}}
const document = {{ getElementById: () => null }};
{chip_preds}
{has_active}
function restoreComposerDraftControls() {{}}
{sticky_helpers}

const messages = [
    {{ role: 'user', content: '/cursor investigate the badge' }},
];
const stickyFirst = {json.dumps(sticky_before_waiting)};

if (stickyFirst) {{
    restoreSessionStickySlash('CH-000503', messages);
}}
const paintedHasAgent = hasActiveCursorAgentChip();
if (!stickyFirst) {{
    restoreSessionStickySlash('CH-000503', messages);
}}
const afterHasAgent = hasActiveCursorAgentChip();

process.stdout.write(JSON.stringify({{
    stickyFirst: stickyFirst,
    paintedHasAgent: paintedHasAgent,
    afterHasAgent: afterHasAgent,
    stickyAfter: (slashCtx.chat.chips || []).map((c) => c.prefix || c.meta || ''),
}}));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    res = json.loads(proc.stdout)

    assert any("/cursor" in p for p in res["stickyAfter"]), res
    assert res["afterHasAgent"] is True, res
    assert res["paintedHasAgent"] is True, (
        "Working-bubble reopen painted while hasActiveCursorAgentChip() was "
        f"{res['paintedHasAgent']!r} (stickyFirst={res['stickyFirst']}). "
        "loadChatSession must restore sticky chips before "
        "updateRemoteWaitingFromMessages / addRemoteWaitingIndicator."
    )


def test_reopen_working_bubble_meta_covers_non_cursor_sticky_agents():
    """Remote-waiting must badge Muse/Codex/… sticky agents, not only Cursor."""
    src = CHAT_JS.read_text(encoding="utf-8")
    indicator = _extract(
        src,
        "    function addRemoteWaitingIndicator(initialStatus) {",
        "    function ensureLocalTypingIndicator(statusText) {",
    )
    ensure_local = _extract(
        src,
        "    function ensureLocalTypingIndicator(statusText) {",
        "    function liveStatusLooksActive(liveStatus) {",
    )
    active_fn = _extract(
        src,
        "    /**\n     * Active sticky agent composer chip (Cursor / Muse / Codex",
        "    /**\n     * Sticky agent chip + preferred model/effort — used when typing UI is",
    )
    meta_fn = _extract(
        src,
        "    /**\n     * Sticky agent chip + preferred model/effort — used when typing UI is",
        "    function syncPreferredModelFromResponse(data) {",
    )

    for body, label in (
        (indicator, "addRemoteWaitingIndicator"),
        (ensure_local, "ensureLocalTypingIndicator"),
    ):
        assert "currentTypingSlashMeta" in body, (
            f"{label} must call currentTypingSlashMeta so reopen badges "
            "Muse/Codex/Hermes/OpenCode (not only Cursor)."
        )
        assert "currentCursorTypingSlashMeta" not in body, (
            f"{label} still calls the Cursor-only helper name."
        )

    assert "CuttleChatSlash.activeStickyAgentChip" in active_fn
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    for agent_fn in ("isStickyMuseAgentChip", "isStickyHermesAgentChip",
                     "isStickyOpenCodeAgentChip", "isStickyCodexAgentChip",
                     "isStickyCursorAgentChip"):
        assert agent_fn in slash_src, f"{agent_fn} must live in chat_slash.js"
    assert "activeStickyAgentChip()" in meta_fn
    assert "hasActiveCursorAgentChip" not in meta_fn
