"""Right-click menu on space tabs: rename, set color, Chrome-style groups."""

from __future__ import annotations

from pathlib import Path

SHELL_JS = Path(__file__).resolve().parents[2] / "src" / "web" / "js" / "app_shell.js"
SHELL_CSS = Path(__file__).resolve().parents[2] / "src" / "web" / "css" / "app_shell.css"


def test_group_color_presets():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "const SPACE_GROUP_COLORS = [" in src
    assert "const SPACE_GROUP_DEFAULT_COLOR = '#e8eaed'" in src  # new groups start white
    for name in ("Grey", "Blue", "Red", "Yellow", "Green", "Pink", "Purple", "Cyan", "Orange"):
        assert name in src
    # Only preset swatches persist; arbitrary strings never become colors.
    assert "function sanitizeSpaceColor(" in src


def test_groups_persist_with_spaces_state():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "groups: cleanGroups" in src
    assert "groups: []" in src
    assert "entry.groupId" in src
    assert "entry.color" in src


def test_tab_menu_offers_rename_color_group():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function openSpaceTabMenu(" in src
    assert "data-ctx-act=\"rename\"" in src
    assert ">Rename<" in src
    assert ">Set color<" in src
    assert ">Add to group<" in src
    assert ">New group<" in src  # group submenu lists New group + existing groups
    assert ">Remove from group<" in src
    assert "host.addEventListener('contextmenu'" in src


def test_group_bubble_edits_name_color_ungroup_delete():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function openSpaceGroupBubbleFor(" in src
    assert 'placeholder="Name group"' in src
    assert ">Ungroup<" in src
    assert ">Delete group<" in src
    assert "Click again to confirm" in src  # delete is a two-step confirm
    assert "function dissolveSpaceGroup(" in src
    assert "function deleteSpaceGroupWithSpaces(" in src


def test_grouped_tabs_render_pill_and_accent():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function spaceGroupPillHtml(" in src
    assert "has-accent" in src
    assert "--space-accent" in src


def test_reorder_drop_joins_or_leaves_group():
    src = SHELL_JS.read_text(encoding="utf-8")
    # Dropping between members of one group joins it; dropping out ungroups.
    assert "function fixDraggedTabGroup(" in src
    assert "fixDraggedTabGroup(draggedId)" in src
    # Parking before the badge never joins from a drag; reordering inside the
    # group (already a member, moved up front) keeps membership.
    assert "already a member" in src
    assert "spaceGroupPillAtX" not in src


def test_buttonless_moves_cancel_drags():
    src = SHELL_JS.read_text(encoding="utf-8")
    # A press whose release was missed must never start a button-less reorder.
    assert "if (e.buttons === 0) { finish(false); return; }" in src
    assert "if (e.buttons === 0) { finish(null, true); return; }" in src
    # pointercancel is a true cancel, never a commit.
    assert "window.addEventListener('pointercancel', onCancel, true)" in src


def test_groups_collapse_and_expand():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function toggleSpaceGroupCollapsed(" in src
    assert "collapsed: !!g.collapsed" in src  # persisted
    assert "group.collapsed) return" in src  # members hidden while collapsed
    assert "shell-space-group-count" in src  # pill shows member count
    # The active tab is never hidden: collapse refuses, switching expands.
    assert "Active space is in this group" in src
    assert "targetGroup.collapsed" in src


def test_open_menu_pins_fullscreen_titlebar():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function isSpaceCtxOpen(" in src
    # The 420ms auto-hide bails while any menu/submenu/bubble is on screen.
    assert "isSpaceCtxOpen()) return;" in src
    # Opening layers drops a hide timer a previous close just armed...
    assert "cuttleTitlebarCancelHide();" in src
    # ...and closing re-arms it when the pointer is away.
    assert "cuttleTitlebarRescheduleHide();" in src


def test_group_tint_css():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert "color-mix" in css
    assert ".shell-space-tab.has-accent.is-active" in css
    assert ".shell-space-group-count" in css


def test_group_menu_css():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".shell-space-group" in css
    assert ".shell-space-tab.has-accent" in css
    assert ".shell-space-ctx" in css
    assert ".shell-group-bubble" in css
    assert ".shell-ctx-swatch" in css
