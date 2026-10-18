"""Phase 7: the frontend composition roots delegate to their owned modules.

One owner per lifetime (``ARCHITECTURE_PRINCIPLES.md`` §1): the chat page and
the app shell gather inputs, call an owner, and apply results. These assertions
pin the *delegation edge* only — the owner declares its namespace and the
composition root holds a thin call site that references it. They deliberately
do **not** slice function bodies out of the root by name; behavior stays with
each owner's own suite.

Backend route-family delegation (blueprint owners) is enforced structurally in
``test_architecture_boundaries.py``; the growth ceilings live in
``test_composition_root_growth.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CHAT_DIR = REPO / "src" / "web" / "js" / "chat"
SHELL_DIR = REPO / "src" / "web" / "js" / "shell"
CHAT_PAGE = CHAT_DIR / "chat_page.js"
APP_SHELL = SHELL_DIR / "app_shell.js"

# (owner file, namespace, root call-site needle)
CHAT_OWNERS = (
    ("chat_session_usage.js", "CuttleChatSessionUsage", "CuttleChatSessionUsage.create("),
    ("chat_media.js", "CuttleChatMedia", "CuttleChatMedia.create("),
    ("chat_tts.js", "CuttleChatTts", "CuttleChatTts.create("),
    ("chat_transcript_nav.js", "CuttleChatTranscriptNav", "CuttleChatTranscriptNav.create("),
    ("chat_subagent_launchers.js", "CuttleChatSubagentLaunchers", "CuttleChatSubagentLaunchers.create("),
    ("chat_agent_controls.js", "CuttleChatAgentControls", "CuttleChatAgentControls.create("),
    ("chat_slash_palette.js", "CuttleChatSlashPalette", "CuttleChatSlashPalette.create("),
    ("chat_context_gauge.js", "CuttleChatContextGauge", "CuttleChatContextGauge.create("),
    ("chat_session.js", "CuttleChatSession", "CuttleChatSession."),
    ("chat_history_panel.js", "CuttleChatHistoryPanel", "CuttleChatHistoryPanel.create("),
    ("chat_stream_transport.js", "CuttleChatStreamTransport", "CuttleChatStreamTransport."),
    ("chat_generation.js", "CuttleChatGeneration", "CuttleChatGeneration."),
    ("chat_pending_result.js", "CuttleChatPendingResult", "CuttleChatPendingResult."),
    ("chat_messages.js", "CuttleChatMessages", "CuttleChatMessages."),
    ("chat_markdown.js", "CuttleChatMarkdown", "CuttleChatMarkdown."),
    ("chat_activate.js", "CuttleChatActivate", "CuttleChatActivate."),
    ("chat_action_cards.js", "CuttleChatActionCards", "CuttleChatActionCards."),
    ("chat_action_forms.js", "CuttleChatActionForms", "CuttleChatActionForms."),
    ("chat_followup_queue.js", "CuttleFollowupQueue", "CuttleFollowupQueue."),
    ("chat_stop_state.js", "CuttleStopState", "CuttleStopState."),
    ("chat_turn_guard.js", "CuttleTurnGuard", "CuttleTurnGuard."),
    ("chat_attention.js", "CuttleChatAttention", "CuttleChatAttention."),
    ("chat_composer.js", "CuttleChatComposer", "CuttleChatComposer."),
    ("chat_attachments.js", "CuttleChatAttachments", "CuttleChatAttachments."),
    ("chat_prompt_history.js", "CuttlePromptHistory", "CuttlePromptHistory."),
    ("chat_project.js", "CuttleChatProject", "CuttleChatProject."),
    ("chat_usage.js", "CuttleChatUsage", "CuttleChatUsage."),
    ("chat_voice.js", "CuttleChatVoice", "CuttleChatVoice.create("),
    ("supervised_control.js", "CuttleSupervised", "CuttleSupervised."),
    ("chat_slash.js", "CuttleChatSlash", "CuttleChatSlash."),
    ("chat_agent_model.js", "CuttleChatAgentModel", "CuttleChatAgentModel."),
    ("chat_stream.js", "CuttleChatStream", "CuttleChatStream."),
    ("chat_usage_live.js", "CuttleUsageLive", "CuttleUsageLive."),
    ("chat_history_view.js", "CuttleChatHistoryView", "CuttleChatHistoryView."),
    ("chat_history_archive.js", "CuttleChatHistoryArchive", "CuttleChatHistoryArchive."),
    ("chat_chip_config.js", "CuttleChipConfig", "CuttleChipConfig."),
    ("chat_worktrees.js", "CuttleChatWorktrees", "CuttleChatWorktrees."),
)

# (owner file, namespace, root call-site needle)
SHELL_OWNERS = (
    ("shell_voice.js", "CuttleShellVoice", "CuttleShellVoice.create("),
    ("shell_electron.js", "CuttleShellElectron", "CuttleShellElectron."),
    ("shell_rail.js", "CuttleShellRail", "CuttleShellRail.create("),
    ("shell_panes.js", "CuttleShellPanes", "CuttleShellPanes.create("),
    ("shell_spaces.js", "CuttleShellSpaces", "CuttleShellSpaces.create("),
)

# Owners that expose top-level globals instead of a namespace (external
# contracts the shell and shared/auth.js call directly).
SHELL_GLOBAL_OWNERS = (
    ("shell_mobile.js", "function cuttleOpenMobileServerSettings(", "cuttleOpenMobileServerSettings()"),
    ("shell_mobile.js", "function cuttleSyncMobileServerRows(", "cuttleSyncMobileServerRows()"),
)


def _declares_namespace(src: str, ns: str) -> bool:
    return re.search(rf"\b(root|window|globalThis)\.{re.escape(ns)}\b", src) is not None


@pytest.mark.parametrize(
    "owner_rel,ns,needle", CHAT_OWNERS, ids=[c[0] for c in CHAT_OWNERS]
)
def test_chat_page_delegates_to_owner(owner_rel, ns, needle):
    owner_src = (CHAT_DIR / owner_rel).read_text(encoding="utf-8")
    assert _declares_namespace(owner_src, ns), (
        f"{owner_rel} must declare namespace {ns}"
    )
    page_src = CHAT_PAGE.read_text(encoding="utf-8")
    assert needle in page_src, (
        f"chat_page.js must hold a thin call site for {ns} ({needle})"
    )


@pytest.mark.parametrize(
    "owner_rel,ns,needle", SHELL_OWNERS, ids=[c[0] for c in SHELL_OWNERS]
)
def test_app_shell_delegates_to_owner(owner_rel, ns, needle):
    owner_src = (SHELL_DIR / owner_rel).read_text(encoding="utf-8")
    assert _declares_namespace(owner_src, ns), (
        f"{owner_rel} must declare namespace {ns}"
    )
    shell_src = APP_SHELL.read_text(encoding="utf-8")
    assert needle in shell_src, (
        f"app_shell.js must hold a thin call site for {ns} ({needle})"
    )


@pytest.mark.parametrize(
    "owner_rel,decl,needle",
    SHELL_GLOBAL_OWNERS,
    ids=[f"{c[0]}:{c[2]}" for c in SHELL_GLOBAL_OWNERS],
)
def test_app_shell_delegates_to_global_owner(owner_rel, decl, needle):
    owner_src = (SHELL_DIR / owner_rel).read_text(encoding="utf-8")
    assert decl in owner_src, f"{owner_rel} must own {decl}"
    shell_src = APP_SHELL.read_text(encoding="utf-8")
    assert needle in shell_src, (
        f"app_shell.js must call the owned global {needle}"
    )


def test_chat_owners_load_before_page_and_shell_owners_before_shell():
    """A delegation edge is real only if the owner loads first (no TDZ)."""
    chat_html = (REPO / "src" / "web" / "chat_page.html").read_text(encoding="utf-8")
    shell_html = (REPO / "src" / "web" / "app_shell.html").read_text(encoding="utf-8")
    page_at = chat_html.index("/js/chat/chat_page.js?v=")
    for owner_rel, _ns, _needle in CHAT_OWNERS:
        marker = f"/js/chat/{owner_rel}"
        assert marker in chat_html, f"{owner_rel} not loaded by chat_page.html"
        assert chat_html.index(marker) < page_at, (
            f"{owner_rel} must load before chat_page.js"
        )
    shell_at = shell_html.index("/js/shell/app_shell.js")
    for owner_rel, _ns, _needle in SHELL_OWNERS:
        marker = f"/js/shell/{owner_rel}"
        assert marker in shell_html, f"{owner_rel} not loaded by app_shell.html"
        assert shell_html.index(marker) < shell_at, (
            f"{owner_rel} must load before app_shell.js"
        )
