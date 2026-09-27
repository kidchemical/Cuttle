"""Follow-up queue must drain on phone when the reply is already visible."""

from __future__ import annotations

from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[2] / "src" / "web" / "js" / "chat_page.js"


def test_heal_clears_stuck_local_generating_when_reply_visible():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "function healStaleGeneratingState()" in src
    assert "finishLocalStreamFromServerSync()" in src
    # Must not bail on isLoading before checking the transcript.
    assert "if (isLoading || userStoppedGeneration" not in src
    assert "if (isLoading && activeEventSource) return false;" in src
    heal = src.split("function healStaleGeneratingState()", 1)[1].split("\n    function ", 1)[0]
    # Previous-turn assistant must not abort this turn (CH-000199-3).
    assert "lastVisibleChatMessageIsAssistant()" not in heal
    assert "turnAlreadyShowsAssistantReply()" in heal
    # Orphan isLoading with no SSE/fetch/in-flight user turn must clear.
    assert "!inFlightUserMessage" in heal


def test_is_session_generating_scopes_local_loading():
    """Bare isLoading from another chat must not park sends in the queue."""
    src = CHAT_JS.read_text(encoding="utf-8")
    fn = src.split("function isSessionGenerating()", 1)[1].split("\n    function ", 1)[0]
    assert "isLoadingThisSession()" in fn
    assert "if (isLoading) return true;" not in fn


def test_create_new_chat_reables_welcome_composer():
    src = CHAT_JS.read_text(encoding="utf-8")
    create = src.split("async function createNewChat()", 1)[1].split("\n    function ", 1)[0]
    assert "detachLocalGenerationForNavigation()" in create
    assert "focusWelcomeComposer()" in create
    assert "function setWelcomeComposerEnabled(enabled)" in src
    load = src.split("async function loadChatSession(sessionId, opts = {})", 1)[1]
    load = load.split("\n    async function ", 1)[0]
    assert "applyServerFollowups(data.followups)" in load
    assert "detachLocalGenerationForNavigation()" in load


def test_live_waiting_uses_this_turn_not_previous_assistant():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "function thisTurnHasAssistantReply(messages)" in src
    waiting = src.split("const liveActiveRaw = liveStatusLooksActive(liveStatus);", 1)[1]
    waiting = waiting.split("const statusLabel", 1)[0]
    assert "thisTurnHasAssistantReply(messages)" in waiting
    assert "transcriptEndsWithAssistant(messages)" not in waiting
    assert "lastVisibleChatMessageIsAssistant()" not in waiting
    assert "if (isLoadingThisSession() && turnAlreadyShowsAssistantReply() && !activeEventSource)" in src
    assert "if (isLoading && lastVisibleChatMessageIsAssistant() && !activeEventSource)" not in src
    assert "if (isLoading && turnAlreadyShowsAssistantReply() && !activeEventSource)" not in src


def test_pending_wait_does_not_require_typing_indicator_gone():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "pending-skip-ui-has-reply" in src
    assert (
        "if (inFlightUserMessage && turnAlreadyShowsAssistantReply()\n"
        "                && !document.getElementById('typing-indicator'))"
        not in src
    )


def test_message_sync_skips_late_assistant_after_stop():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "if (msg.role === 'assistant' && userStoppedGeneration)" in src
    assert "paint the reply the user cancelled" in src


def test_iframe_uses_parent_visibility_for_background():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "function pageIsBackgrounded()" in src
    assert "window.parent.document.hidden" in src
    assert "if (pageIsBackgrounded()) return;" in src
    assert "if (!recoveringLocal && isSessionGenerating()) return;" not in src


def test_turn_already_shows_requires_exact_match():
    """Fuzzy suffix match must not suppress painting a new reply (chirp, no bubble)."""
    src = CHAT_JS.read_text(encoding="utf-8")
    fn = src.split("function turnAlreadyShowsAssistantReply(content)", 1)[1].split(
        "\n    function ", 1
    )[0]
    assert "raw === want" in fn
    assert "messageElMatchesText(el, content)" not in fn
    assert "chirp-heal-missing-bubble" in src
    assert "openTranscriptMissingThisTurnReply" in src
