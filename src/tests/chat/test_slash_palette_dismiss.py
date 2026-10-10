"""Slash palette dismisses on click-off, like Escape.

Regression: opening the `/` palette in a chat and clicking elsewhere left
the menu open; only Escape closed it. A document-level capture pointerdown
now calls ``hideMenu(key, {dismiss: true})`` for any open menu whose
pointer target is outside both the menu and its composer textarea.

Owned by ``chat_slash_palette.js`` (CuttleChatSlashPalette) since the 2B
palette extraction; chat_page.js keeps thin delegating call sites.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PALETTE_JS = REPO / "src" / "web" / "js" / "chat" / "chat_slash_palette.js"
CHAT_JS = REPO / "src" / "web" / "js" / "chat" / "chat_page.js"


def test_palette_owner_exposes_controller():
    src = PALETTE_JS.read_text(encoding="utf-8")
    assert "CuttleChatSlashPalette" in src
    assert "function create(deps) {" in src


def test_palette_click_off_listener_registered():
    src = PALETTE_JS.read_text(encoding="utf-8")
    anchor = "Click / tap outside an open palette"
    assert anchor in src
    block = src.split(anchor, 1)[1].split("}, true);", 1)[0]
    assert "doc.addEventListener('pointerdown'" in block
    assert "hideMenu(key, { dismiss: true })" in block


def test_palette_click_off_skips_menu_and_composer():
    src = PALETTE_JS.read_text(encoding="utf-8")
    anchor = "Click / tap outside an open palette"
    block = src.split(anchor, 1)[1].split("}, true);", 1)[0]
    # Menu-item picks (click) and textarea focus must not dismiss first.
    assert "menu.contains(t)" in block
    assert "'chatInput'" in block and "'welcomeChatInput'" in block
    # Covers both composers, and only open menus.
    assert "menu.hidden" in block
    assert "menuId(key)" in block


def test_palette_dismiss_keeps_token_but_stays_closed():
    src = PALETTE_JS.read_text(encoding="utf-8")
    # dismiss:true sets paletteDismissed so typing more `/…` text does not
    # reopen; clearing the token resets it (syncFromInput no-parse path).
    assert "ctx[key].paletteDismissed = true;" in src
    assert "ctx[key].paletteDismissed = false;" in src


def test_chat_page_delegates_palette_lifecycle_to_owner():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "CuttleChatSlashPalette.create(" in src
    # Thin call sites, no private palette DOM state left in the page.
    assert "return slashPalette.syncFromInput(textarea);" in src
    assert "return slashPalette.hideMenu(key, opts);" in src
    assert "return slashPalette.handleKeyDown(event, textarea);" in src
    assert "renderSlashMenu" not in src
