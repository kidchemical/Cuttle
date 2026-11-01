"""A choice card written before a later tool call still reaches the final reply."""
from scripts.utilities.claude_stream import carry_ui_blocks

CARD = '<cuttle_action_form>{"title": "Pick", "fields": []}</cuttle_action_form>'
CONFIRM = '<cuttle_confirm action="x.y">Do it?</cuttle_confirm>'


def test_card_from_an_earlier_segment_is_appended():
    out = carry_ui_blocks(["Here is the answer.\n\n" + CARD], "Saved.")
    assert out.startswith("Saved.") and out.endswith(CARD)


def test_blocks_already_in_the_final_reply_are_not_duplicated():
    final = "Answer.\n\n" + CARD
    assert carry_ui_blocks([CARD, CARD], final) == final


def test_confirms_carry_and_plain_text_does_not():
    out = carry_ui_blocks(["just notes", "more " + CONFIRM], "Done.")
    assert out == "Done.\n\n" + CONFIRM
    assert carry_ui_blocks(["just notes"], "Done.") == "Done."
