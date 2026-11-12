"""Unattended chats (sub-agent children, live-supervised targets) get no user-click guidance.

Incident 2026-11-10: a supervised orchestrator emitted a Discord card nobody could
click, and its supervisor told the user to go click it.
"""
import re

from api.cuttle_brain import context_compiler as cc
from api.cuttle_ui_capabilities import cuttle_ui_capabilities_block, unattended_note


def _runbook_listed(text, name):
    return bool(re.search(rf"^\s+{re.escape(name)}\s+—", text, re.M))


def test_capabilities_drop_card_and_discord_runbooks_by_reason():
    plain = cuttle_ui_capabilities_block()
    assert _runbook_listed(plain, "discord.md") and _runbook_listed(plain, "action-forms.md")
    assert "Prefer clickable forms" in plain

    supervised = cuttle_ui_capabilities_block(unattended="supervised")
    assert not _runbook_listed(supervised, "discord.md")
    # The supervisor answers Q&A cards, so the forms runbook stays.
    assert _runbook_listed(supervised, "action-forms.md")

    child = cuttle_ui_capabilities_block(unattended="subagent")
    assert not _runbook_listed(child, "discord.md") and not _runbook_listed(child, "action-forms.md")
    assert "Prefer clickable forms" not in child
    assert _runbook_listed(child, "subagents.md")


def test_unattended_note_overrides_rules_and_lands_after_them(monkeypatch):
    assert unattended_note("") == ""
    assert "Q&A card" in unattended_note("supervised")
    assert "cuttle_action_form" in unattended_note("subagent")
    for reason, label in (("supervised", "supervised"), ("subagent", "sub-agent")):
        monkeypatch.setattr(cc, "unattended_reason", lambda _sid, r=reason: r)
        compiled = cc.compile_context("hi", chat_session_id=4242)
        env = compiled.envelope
        assert "unattended" in compiled.layers_used
        assert f"## Unattended chat ({label})" in env
        # After the rules, so it wins over a project rule that demands a card.
        assert env.index("## Unattended chat") > env.index("## Profile")


def test_user_driven_chat_keeps_full_guidance(monkeypatch):
    monkeypatch.setattr(cc, "unattended_reason", lambda _sid: "")
    compiled = cc.compile_context("hi", chat_session_id=4242)
    assert "unattended" not in compiled.layers_used
    assert "## Unattended chat" not in compiled.envelope
    assert _runbook_listed(compiled.envelope, "discord.md")
