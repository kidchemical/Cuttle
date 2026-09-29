"""Tests for invisible Cuttle UI capability injection."""

from pathlib import Path

from api.cuttle_ui_capabilities import (
    CUTTLE_UI_CAPABILITIES_TEXT,
    cuttle_ui_system_addon,
    strip_cuttle_ui_capabilities,
    with_cuttle_ui_capabilities,
)

_GLOBAL_DOCS = Path(__file__).resolve().parents[2] / ".cuttle_global" / "docs"


def test_with_cuttle_ui_capabilities_prefixes_block():
    out = with_cuttle_ui_capabilities("post discord update")
    assert out.startswith("<cuttle_ui_capabilities>")
    assert "</cuttle_ui_capabilities>" in out
    assert out.endswith("post discord update")
    assert "action-forms.md" in out


def test_with_cuttle_ui_capabilities_idempotent():
    once = with_cuttle_ui_capabilities("hello")
    twice = with_cuttle_ui_capabilities(once)
    assert twice.count("<cuttle_ui_capabilities>") == 1


def test_with_cuttle_ui_capabilities_empty():
    assert with_cuttle_ui_capabilities("") == ""
    assert with_cuttle_ui_capabilities("   ") == ""


def test_with_cuttle_ui_capabilities_skip_inject_on_resume():
    assert with_cuttle_ui_capabilities("hello", inject=False) == "hello"
    assert "<cuttle_ui_capabilities>" not in with_cuttle_ui_capabilities("hello", inject=False)
    assert with_cuttle_ui_capabilities("hello", inject=True).startswith("<cuttle_ui_capabilities>")


def test_strip_cuttle_ui_capabilities_removes_echo():
    body = (
        "<cuttle_ui_capabilities>\n"
        + CUTTLE_UI_CAPABILITIES_TEXT
        + "\n</cuttle_ui_capabilities>\n\n"
        "Here is a form:\n"
        "<cuttle_action_form>{}</cuttle_action_form>"
    )
    out = strip_cuttle_ui_capabilities(body)
    assert "<cuttle_ui_capabilities>" not in out.lower()
    assert "cuttle_action_form" in out
    assert out.startswith("Here is a form:")


def test_system_addon_points_at_hub_docs():
    addon = cuttle_ui_system_addon()
    assert "action-forms.md" in addon
    assert ".cuttle" in addon


def test_capabilities_are_directory_not_howto():
    text = CUTTLE_UI_CAPABILITIES_TEXT
    for name in (
        "action-forms.md",
        "agent-ops-cli.md",
        "charts.md",
        "chat-media.md",
        "discord.md",
        "chat-history.md",
        "headless-turns.md",
    ):
        assert name in text
        assert (_GLOBAL_DOCS / name).is_file()
    # Intent triggers for routing (not how-to).
    assert "cuttle_action_form" in text
    assert "CH-" in text
    assert "vega" in text.lower()
    assert "one-shot" in text.lower()
    assert "chat-media.md" in text
    assert "/output/shared" in text or "markdown" in text.lower()
    # Gotchas / recipes stay out of the master injection.
    assert "__watch_" not in text
    assert "Restart Flask (daemon-owned)" not in text
    assert "sqlite3" not in text
    assert "force-kill" in text.lower() or "taskkill" in text.lower() or "stop-process" in text.lower()
    assert "personal" in text.lower()
