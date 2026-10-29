"""Agent-facing transcript text never carries another model's reasoning."""

from api.transcript_text import agent_visible_metadata, agent_visible_text


def test_strips_original_wrapper_and_think_blocks():
    text = (
        '<cuttle_original_response>"<think>\\nplan\\n</think>\\n\\nraw"</cuttle_original_response>'
        "\n\n<thinking>more</thinking>Visible answer."
    )
    assert agent_visible_text(text) == "Visible answer."


def test_unclosed_think_tag_keeps_text_without_framing():
    assert agent_visible_text("<think>\nclipped narration…") == "clipped narration…"


def test_plain_text_is_untouched():
    assert agent_visible_text("  a < b  ") == "a < b"
    assert agent_visible_text("plain") == "plain"
    assert agent_visible_text(None) == ""


def test_metadata_drops_original_response_only():
    meta = {"postprocessing": {"status": "completed", "original_response": "<think>x</think>"}, "k": 1}
    out = agent_visible_metadata(meta)
    assert out == {"postprocessing": {"status": "completed"}, "k": 1}
    assert meta["postprocessing"]["original_response"]  # input not mutated
    assert agent_visible_metadata(None) is None
