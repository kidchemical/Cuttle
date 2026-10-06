"""Unit tests for Cursor Agent reply assembly (multi-turn stream → chat text)."""

from scripts.utilities.cursor_cli_tool import (
    _assemble_cursor_agent_reply,
    _merge_cursor_assistant_delta,
    _peel_cursor_final_answer,
    _unglue_cursor_result_text,
)


def test_unglue_smashed_sentence_boundaries():
    raw = (
        "I'll investigate the blur overlay — those often share a bug."
        "The messages API expects a numeric id."
        "Checking how IDs are normalized elsewhere."
        "Confirmed: db_session_59 404s."
    )
    out = _unglue_cursor_result_text(raw)
    assert "bug.\n\nThe messages" in out
    assert "id.\n\nChecking" in out
    assert "elsewhere.\n\nConfirmed:" in out


def test_assemble_multi_turn_puts_interim_in_think_block():
    turns = [
        "I'll investigate the chat history load path.",
        "The messages API expects a numeric session id.",
        "Confirmed: db_session_59 404s. Fixing both.",
    ]
    final = (
        "I'll investigate the chat history load path."
        "The messages API expects a numeric session id."
        "Confirmed: db_session_59 404s. Fixing both."
        "Two bugs, both fixed:\n\n1. Empty history\n2. Stuck blur"
    )
    out = _assemble_cursor_agent_reply(
        turns,
        delta_buf="Two bugs, both fixed:\n\n1. Empty history\n2. Stuck blur",
        final_text=final,
    )
    assert out.startswith("<think>\n")
    assert "I'll investigate the chat history load path." in out
    assert "Confirmed: db_session_59 404s. Fixing both." in out
    assert "</think>\n\nTwo bugs, both fixed:" in out
    # Visible answer must not glue interim narration onto the summary.
    visible = out.split("</think>", 1)[1]
    assert "I'll investigate" not in visible
    assert "Empty history" in visible


def test_assemble_single_turn_no_think_block():
    out = _assemble_cursor_agent_reply([], delta_buf="All done.", final_text="All done.")
    assert out == "All done."
    assert "<think>" not in out


def test_assemble_falls_back_to_unglued_final_text():
    final = "Root cause found.Fixing the overlay next."
    out = _assemble_cursor_agent_reply([], delta_buf="", final_text=final)
    assert out == "Root cause found.\n\nFixing the overlay next."


# ── regression: query 7c5516da — stream rewrite produced doubled answer ────────

def test_merge_delta_rewrite_replaces_not_appends():
    """A large rewritten chunk must replace the buffer, not glue two answers."""
    buf = "The Electron app uses :800, not :8000."
    rewrite = "The Electron app uses :8000, not :800."
    result = _merge_cursor_assistant_delta(buf, rewrite)
    assert result == rewrite
    assert result.count("The Electron app") == 1


def test_merge_delta_prefix_growth():
    """Normal incremental append (continuation) must still grow the buffer."""
    buf = "Electron is hard"
    piece = "Electron is hardwired to https://127.0.0.1:8080"
    result = _merge_cursor_assistant_delta(buf, piece)
    assert result == piece


def test_merge_delta_small_continuation():
    """Small continuation at end: still append."""
    buf = "Here is the fix:\n\n"
    piece = "1. Log in on Electron"
    result = _merge_cursor_assistant_delta(buf, piece)
    assert result == buf + piece


def test_peel_strips_interim_turns():
    interim = [
        "I'll trace the history load path.",
        "Found the issue: different origin.",
    ]
    final = (
        "I'll trace the history load path."
        "Found the issue: different origin."
        "Most likely cause: different storage/auth contexts."
    )
    out = _peel_cursor_final_answer(final, interim)
    assert out == "Most likely cause: different storage/auth contexts."
    assert "I'll trace" not in out


def test_assemble_no_double_answer_when_result_duplicates_delta():
    """Regression: result event restating the streaming delta must not double the answer."""
    turns = ["I'll trace how history is sourced in Electron vs Chrome."]
    answer = "Most likely cause: different storage/auth contexts.\n\nElectron uses :8080."
    # The result field concatenates interim + answer (Cursor SDK behaviour)
    final = "I'll trace how history is sourced in Electron vs Chrome." + answer
    out = _assemble_cursor_agent_reply(turns, delta_buf=answer, final_text=final)
    visible = out.split("</think>", 1)[1]
    assert visible.count("Most likely cause") == 1
    assert visible.count("Electron uses :8080") == 1


def test_assemble_promotes_markdown_deliverable_over_short_closer():
    """Mid-run table must stay visible when a later tool ends on a short status."""
    table = (
        "### Gauge gaps closed\n\n"
        "| Agent | Gauge |\n|---|---|\n| Cursor | yes |\n| Hermes | yes |"
    )
    closer = "The Hermes probe finished; compact path updated."
    out = _assemble_cursor_agent_reply(
        [
            "I'll verify Antigravity and Hermes gauge wiring.",
            table,
        ],
        delta_buf=closer,
        final_text="",
    )
    assert out.startswith("<think>\n")
    assert "I'll verify Antigravity" in out
    visible = out.split("</think>", 1)[1].strip()
    assert "### Gauge gaps closed" in visible
    assert "| Hermes | yes |" in visible
    # Short closer may also surface when it is not progress-shaped.
    assert "I'll verify Antigravity" not in visible


def test_assemble_promotes_action_form_over_short_closer():
    form = (
        '<cuttle_action_form>\n'
        '{"mode":"choice","title":"Restart Flask","options":[]}\n'
        "</cuttle_action_form>"
    )
    out = _assemble_cursor_agent_reply(
        ["I'll restart Flask after the Python changes."],
        delta_buf=form,
        final_text="",
    )
    # Form is the only deliverable; promise is think; form is visible.
    # (If form is last and only answer, promise in think.)
    assert "<cuttle_action_form>" in out
    if "</think>" in out:
        visible = out.split("</think>", 1)[1]
    else:
        visible = out
    assert "<cuttle_action_form>" in visible
    assert "I'll restart Flask" not in visible


def test_assemble_peel_does_not_shrink_long_stream_answer():
    """Shorter result peel must not replace a longer structured stream answer."""
    long_answer = (
        "## RCA\n\n"
        "1. Tool flushes bury the deliverable.\n"
        "2. Last turn was only a status closer.\n\n"
        "Fix: classify progress vs answer turns."
    )
    turns = ["I'll dig into reply assembly."]
    # Result peels to a short remnant after stripping the promise.
    final = "I'll dig into reply assembly." + "Done."
    out = _assemble_cursor_agent_reply(
        turns, delta_buf=long_answer, final_text=final
    )
    visible = out.split("</think>", 1)[1].strip() if "</think>" in out else out
    assert "## RCA" in visible
    assert "classify progress" in visible
    assert visible.strip() != "Done."
