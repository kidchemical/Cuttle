"""The chat-store prompt block must never hand an agent someone else's session.

Regression from CH-000155: the addon that teaches a CLI agent where Cuttle
transcripts live shipped a copy-pasteable read recipe containing a *literal*
session id (`sid=147`) borrowed from the doc example. Asked about the chat it
was running in, the agent ran the recipe verbatim, read `chat_session_id=147`,
and answered about CH-000147 — a different user conversation.

Two properties keep that from happening again:

1. The block never contains a runnable literal session id that is not the
   current chat (no id to copy = nothing to leak).
2. When Cuttle knows which chat the turn belongs to, the block says so and the
   recipe is scoped to that id.
"""

from __future__ import annotations

import re

import pytest

from api.cuttle_ui_capabilities import cuttle_chat_store_addon, numeric_chat_session_id

# `sid=147`, `sid = 147`, `chat_session_id=147`, ... — an id an agent can run as-is.
_LITERAL_SID_RE = re.compile(
    r"\b(?:sid|chat_session_id|session_id)\s*=\s*(\d+)",
    re.IGNORECASE,
)

# The bare `CH-000147` handle that seeded the leak.
_LITERAL_HANDLE_RE = re.compile(r"\bCH-(\d{3,})\b", re.IGNORECASE)


def _literal_sids(text: str) -> list[int]:
    return [int(m) for m in _LITERAL_SID_RE.findall(text)]


def _literal_handles(text: str) -> list[int]:
    return [int(m) for m in _LITERAL_HANDLE_RE.findall(text)]


# --------------------------------------------------------------------------
# 1 — no session id an agent can copy when Cuttle did not name one
# --------------------------------------------------------------------------


def test_addon_carries_no_borrowed_session_id():
    """CH-000155: the recipe shipped `sid=147`, so the agent read CH-000147."""
    addon = cuttle_chat_store_addon()

    assert not _literal_sids(addon), (
        "the chat-store recipe assigns a literal session id with no chat to "
        "justify it; an agent will run it verbatim and read that stranger's "
        f"transcript: {_literal_sids(addon)}"
    )
    assert not _literal_handles(addon), (
        "a concrete CH- handle in the prompt block is copy-paste bait; state "
        f"the id→row mapping without a real session: {_literal_handles(addon)}"
    )


def test_addon_still_teaches_via_runbook_pointer():
    """Thin addon points at the runbook; how-to lives there."""
    from pathlib import Path

    addon = cuttle_chat_store_addon()
    assert "chat-history.md" in addon
    assert "gitignored" in addon.lower() or "not in the working tree" in addon.lower()

    runbook = Path(__file__).resolve().parents[3] / ".cuttle_global" / "docs" / "chat-history.md"
    body = runbook.read_text(encoding="utf-8")
    assert "chat_sessions" in body and "chat_messages" in body
    assert "leading zeros" in body.lower()
    assert "CH-<session>-<N>" in body or "1-based" in body


# --------------------------------------------------------------------------
# 2 — when the turn's chat is known, scope the recipe to it
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handle",
    [155, "155", "CH-000155", "db_session_155", "CH-000155-9"],
)
def test_addon_scopes_the_recipe_to_the_current_chat(handle):
    """Every id shape the callers hold must resolve to the same chat."""
    addon = cuttle_chat_store_addon(current_session_id=handle)

    assert "CH-000155" in addon, "the agent should be told which chat it is in"
    assert _literal_sids(addon) == [155], (
        f"briefing must name only the current chat, got {_literal_sids(addon)}"
    )
    assert 147 not in _literal_handles(addon)
    assert "chat-history.md" in addon


def test_unresolvable_session_ids_do_not_invent_a_chat():
    """A non-numeric handle must degrade to 'no id', never to a guess."""
    for handle in (None, "", "muse-chat-discovery", "unknown", "CH-000155-0"):
        addon = cuttle_chat_store_addon(current_session_id=handle)
        assert not _literal_sids(addon), f"{handle!r} produced a literal sid"


def test_numeric_chat_session_id_only_accepts_known_handle_shapes():
    assert numeric_chat_session_id(155) == 155
    assert numeric_chat_session_id("CH-000155") == 155
    assert numeric_chat_session_id("db_session_155") == 155
    assert numeric_chat_session_id("CH-000155-9") == 155
    assert numeric_chat_session_id("muse-chat-discovery") is None
    assert numeric_chat_session_id("CH-000155-0") is None
    assert numeric_chat_session_id(None) is None
    assert numeric_chat_session_id(0) is None


def test_parse_chat_handle_message_ref():
    from api.cuttle_ui_capabilities import parse_chat_handle

    assert parse_chat_handle("CH-000182-23") == {
        "session_id": 182,
        "message_index": 23,
    }
    assert parse_chat_handle("CH-000182") == {
        "session_id": 182,
        "message_index": None,
    }
    assert parse_chat_handle("CH-000182-0") is None


# --------------------------------------------------------------------------
# 3 — the callers that build real prompts must pass the chat through
# --------------------------------------------------------------------------


def test_muse_prompt_scopes_the_chat_store_block_to_this_chat(monkeypatch):
    """`/muse what did we say earlier?` must point at *this* chat, not 147."""
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod

    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "native", "path": "/x"})
    monkeypatch.setattr(w, "_build_shell_pane_prompt_addon", lambda _p: "")

    prompt = "summarize the history of this chat"
    enriched = w._with_muse_chat_context(prompt, chat_session_id=155)

    assert "Cuttle chat history" in enriched
    assert "CH-000155" in enriched
    assert _literal_sids(enriched) == [155]
    assert enriched.endswith(prompt)


def test_muse_prompt_never_leaks_a_foreign_id_without_a_session(monkeypatch):
    """Callers that have no session id must not fall back to the old example."""
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod

    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "native", "path": "/x"})
    monkeypatch.setattr(w, "_build_shell_pane_prompt_addon", lambda _p: "")

    enriched = w._with_muse_chat_context("what chats do I have open?")

    assert "Cuttle chat history" in enriched
    assert not _literal_sids(enriched)


def test_compiled_context_scopes_the_chat_store_hint(tmp_path):
    """Context Compiler builds the same block for every harness agent."""
    from api.cuttle_brain.context_compiler import compile_context

    compiled = compile_context(
        "what did we decide in this chat?",
        project_path=str(tmp_path),
        include_chat_store_hint=True,
        chat_session_id="db_session_155",
    )

    assert "CH-000155" in compiled.envelope
    assert _literal_sids(compiled.envelope) == [155]
    assert compiled.prompt.endswith("what did we decide in this chat?")


def test_compiled_context_without_a_session_has_no_literal_id(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context

    compiled = compile_context(
        "what did we decide in this chat?",
        project_path=str(tmp_path),
        include_chat_store_hint=True,
    )

    assert "Cuttle chat history" in compiled.envelope
    assert not _literal_sids(compiled.envelope)
