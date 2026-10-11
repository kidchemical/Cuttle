"""chat_page.js must parse — a SyntaxError blanks the entire chat UI.

Regression: commit 73341e4 dropped ``function _historyActionIcons() {`` while
rewiring history reload, leaving a bare ``return {…}`` then ``let`` → browsers
reported ``Unexpected identifier 'let'`` and the page never loaded (phone +
desktop).
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
CHAT_HTML = Path(__file__).resolve().parents[2] / "web" / "chat_page.html"
GIT_PUSH_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "git/git_push_report.js"


@node_only
def test_chat_page_js_parses():
    proc = subprocess.run(
        ["node", "--check", str(CHAT_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        "chat_page.js failed node --check (UI will not load):\n"
        + (proc.stderr or proc.stdout or "(no output)")
    )


def test_history_action_icons_function_present():
    """Guard the exact deletion that broke parse: header must precede its body."""
    text = CHAT_JS.read_text(encoding="utf-8")
    assert "function _historyActionIcons() {" in text


def test_save_chat_session_keep_search_moved_to_owner():
    """3B step 2 moved saveChatSession (and its keep-search reload anchor) into
    chat_session.js; the page keeps a delegating call site."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = (CHAT_JS.parent / "chat_session.js").read_text(encoding="utf-8")
    assert "// Reload chat history to update sidebar (keep active search/filters)" in owner
    assert "h.reloadHistoryListKeepingSearch();" in owner
    assert "return sessionLifecycle().saveChatSession(message, role, opts);" in text


def test_load_chat_session_moved_to_owner():
    """3B step 3 moved loadChatSession into chat_session.js (CuttleChatSession
    .createLoader); the page keeps a thin delegating call site."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = (CHAT_JS.parent / "chat_session.js").read_text(encoding="utf-8")
    assert "function createLoader(session, host) {" in owner
    assert "async function loadChatSession(sessionId, opts = {})" in owner
    assert "return { loadChatSession: loadChatSession };" in owner
    assert "createLoader: createLoader" in owner
    assert "return sessionLoader().loadChatSession(sessionId, opts);" in text


def test_create_new_chat_and_boot_restore_moved_to_owner():
    """3B step 4 moved createNewChat and bootRestoreSession into chat_session.js
    (CuttleChatSession.createSessionBoot); the page keeps thin call sites."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = (CHAT_JS.parent / "chat_session.js").read_text(encoding="utf-8")
    assert "function createSessionBoot(session, host) {" in owner
    assert "async function createNewChat()" in owner
    assert "async function bootRestoreSession(sessionParam)" in owner
    assert "createSessionBoot: createSessionBoot" in owner
    assert "return sessionBoot().createNewChat();" in text
    assert "return sessionBoot().bootRestoreSession(sessionParam);" in text


def test_chat_page_html_loads_busted_script():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert 'src="/js/chat/chat_page.js?v=' in html


def test_followup_execution_and_steering_moved_to_owner():
    """4D moved follow-up execution + steering into chat_followup_queue.js
    (CuttleFollowupQueue.createController); the page keeps thin call sites."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = (CHAT_JS.parent / "chat_followup_queue.js").read_text(encoding="utf-8")
    assert "function createController(host) {" in owner
    assert "async function drain() {" in owner
    assert "async function steer(message, attachments) {" in owner
    assert "CuttleFollowupQueue.createController(" in text
    assert "followupController().drain()" in text
    assert "followupController().steer(message, attachments)" in text
    # The page keeps no private queue/edit/timer state and no decision bodies.
    assert "let followupQueue = CuttleFollowupQueue.createQueueState();" not in text
    assert "let editingFollowupId = null;" not in text
    assert "followupDrainTimer" not in text
    assert "function persistFollowupPut(" not in text
    assert "function rememberUnsyncedQueue(" not in text
    assert "CuttleFollowupQueue.reconcileServerList(" not in text


def test_git_push_menu_moved_to_owner():
    """3E pt2 moved the git push menu/action into git_push_report.js
    (CuttleGitPushReport.create); the page keeps thin call sites."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = GIT_PUSH_JS.read_text(encoding="utf-8")
    assert "function create(host) {" in owner
    assert "function menuHtml(icons) {" in owner
    assert "function openSubmenu(triggerBtn) {" in owner
    assert "function setupConfirmModal() {" in owner
    assert "const gitPush = CuttleGitPushReport.create({" in text
    assert "gitPush.menuHtml(icons)" in text
    assert "refreshGitPushMenuMode: gitPush.refreshMenuMode," in text
    assert "gitPush.openSubmenu(trigger)" in text
    assert "gitPush.push(e, root || null)" in text
    assert "gitPush.setupConfirmModal();" in text
    assert "gitPush.refreshMenuMode(dropdown);" in text
    # The old page-side implementations are gone (one owner, no duplicates).
    assert "function gitPushMenuHtml(" not in text
    assert "function applyGitPushMenuMode(" not in text
    assert "function refreshGitPushMenuMode(" not in text
    assert "function pushCurrentChatProject(" not in text
    assert "function openGitPushSubmenu(" not in text
    assert "function setupGitPushConfirmModal(" not in text


def test_supervised_activity_cards_moved_to_owner():
    """4F moved supervised activity-card rendering/coordination into
    supervised_control.js (CuttleSupervised.createController); the page keeps
    thin call sites and no private card state or decision bodies."""
    text = CHAT_JS.read_text(encoding="utf-8")
    owner = (CHAT_JS.parent / "supervised_control.js").read_text(encoding="utf-8")
    assert "function createController(host) {" in owner
    assert "function upsertActivityCard(task) {" in owner
    assert "function wireActivityCard(card) {" in owner
    assert "async function dispatchTaskControl(taskId, action, params = {}) {" in owner
    assert "async function dispatchControlMessage(message, opts = {}) {" in owner
    assert "function updateTaskIndicator(liveStatus, sessionId) {" in owner
    assert "CuttleSupervised.createController(" in text
    assert "supervisedController().showJumpNotification(task)" in text
    assert "supervisedController().upsertActivityCard(task)" in text
    assert "supervisedController().wireActivityCard(card)" in text
    assert "supervisedController().updateTaskIndicator(liveStatus, sessionId)" in text
    # The page keeps no private supervised state and no decision bodies.
    assert "const _supervisedJumpAck = new Set(" not in text
    assert "let _supervisedBubbleObserver = null;" not in text
    assert "const SUPERVISED_PHASE_LABELS" not in text
    assert "function acknowledgeSupervisedJump(" not in text
    assert "function scrollToSupervisedBubble(" not in text
    assert "dispatchSupervisedTaskControl._inflight" not in text
    assert "dispatchSupervisedControlMessage._inflight" not in text
    assert "supervised-activity-row" in owner  # only removed / hidden, not mounted
