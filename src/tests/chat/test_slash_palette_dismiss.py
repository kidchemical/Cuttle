"""Slash palette dismisses on click-off, like Escape.

Regression: opening the `/` palette in a chat and clicking elsewhere left
the menu open; only Escape closed it. A document-level capture pointerdown
now calls ``hideSlashMenu(key, {dismiss: true})`` for any open menu whose
pointer target is outside both the menu and its composer textarea.
"""

from __future__ import annotations

from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat" / "chat_page.js"


def test_palette_click_off_listener_registered():
    src = CHAT_JS.read_text(encoding="utf-8")
    anchor = "Click / tap outside an open palette"
    assert anchor in src
    block = src.split(anchor, 1)[1].split("}, true);", 1)[0]
    assert "document.addEventListener('pointerdown'" in block
    assert "hideSlashMenu(key, { dismiss: true })" in block


def test_palette_click_off_skips_menu_and_composer():
    src = CHAT_JS.read_text(encoding="utf-8")
    anchor = "Click / tap outside an open palette"
    block = src.split(anchor, 1)[1].split("}, true);", 1)[0]
    # Menu-item picks (click) and textarea focus must not dismiss first.
    assert "menu.contains(t)" in block
    assert "'chatInput'" in block and "'welcomeChatInput'" in block
    # Covers both composers, and only open menus.
    assert "menu.hidden" in block
    assert "slashMenuId(key)" in block


def test_palette_dismiss_keeps_token_but_stays_closed():
    src = CHAT_JS.read_text(encoding="utf-8")
    # dismiss:true sets paletteDismissed so typing more `/…` text does not
    # reopen; clearing the token resets it (syncSlashMenuFromInput no-parse path).
    assert "slashCtx[key].paletteDismissed = true;" in src
    assert "slashCtx[key].paletteDismissed = false;" in src
