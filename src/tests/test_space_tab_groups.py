"""Right-click menu on space tabs: rename, set color, Chrome-style groups."""

from __future__ import annotations

from pathlib import Path

SHELL_JS = Path(__file__).resolve().parents[2] / "src" / "web" / "js" / "app_shell.js"
SHELL_CSS = Path(__file__).resolve().parents[2] / "src" / "web" / "css" / "app_shell.css"
SPACES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "js" / "spaces"
STATE_JS = SPACES_DIR / "spaces_state.js"
GROUPS_JS = SPACES_DIR / "spaces_groups.js"
ORDER_JS = SPACES_DIR / "spaces_order.js"
DROP_JS = SPACES_DIR / "spaces_drop.js"


def test_group_color_presets():
    # Palette owner is spaces_state.js (single source of truth, Phase 1);
    # the shell consumes CuttleSpaces.COLORS / sanitizeColor / DEFAULT_COLOR.
    mod = STATE_JS.read_text(encoding="utf-8")
    assert "const COLORS = [" in mod
    assert "const DEFAULT_COLOR = '#e8eaed'" in mod  # new groups start white
    for name in ("White", "Grey", "Blue", "Red", "Yellow", "Green", "Pink", "Purple", "Cyan", "Orange"):
        assert name in mod
    # Only preset swatches persist; arbitrary strings never become colors.
    assert "function sanitizeColor(" in mod
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "const SPACE_GROUP_COLORS = [" not in src  # no forked copy
    assert "const SPACE_GROUP_DEFAULT_COLOR" not in src
    assert "CuttleSpaces.sanitizeColor" in src


def test_groups_persist_with_spaces_state():
    # Load/save sanitation owner is spaces_state.js; the shell binds storage.
    mod = STATE_JS.read_text(encoding="utf-8")
    assert "groups: cleanGroups" in mod
    assert "groups: []" in mod
    assert "entry.groupId" in mod
    assert "entry.color" in mod
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "CuttleSpaces.loadSpacesState(" in src
    assert "CuttleSpaces.saveSpacesState(" in src


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
    # Phase 1: one canonical target (spaces_drop.js) feeds preview AND commit.
    # The old split (fixDraggedTabGroup + sleeve-rect neighbor pass) is gone.
    mod = DROP_JS.read_text(encoding="utf-8")
    assert "function computeDropTarget(" in mod
    assert "function applyDropTarget(" in mod
    assert "spaceGroupPillAtX" not in mod
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function fixDraggedTabGroup(" not in src
    assert "computeSpaceDropTarget(host, tab.dataset.spaceId" in src  # preview + commit
    assert "CuttleSpaces.applyDropTarget(spacesState, target," in src
    assert "spaceGroupPillAtX" not in src


def test_tab_reorder_survives_collapsed_groups():
    # Hidden members keep their slots; the dragged tab anchors after its
    # left visible neighbor instead of the all-or-nothing bail.
    # Owner is spaces_order.js (Phase 1); the shell commits via applyDropTarget.
    mod = ORDER_JS.read_text(encoding="utf-8")
    assert "function computeReorderedIds(" in mod
    assert "function applyVisibleOrder(" in mod
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function reorderSpacesAroundHidden(" not in src
    assert "reorderSpaces(ids)" not in src
    # Off-window releases snap back instead of floating the tab.
    assert "window.addEventListener('blur', () => { if (drag) finish(false); });" in src
    assert "window.addEventListener('blur', onBlurCancel, true)" in src


def test_tab_drag_nudges_groups_live():
    src = SHELL_JS.read_text(encoding="utf-8")
    # Tabs and sleeves share the measured lane order for the canonical target.
    assert "'.shell-space-tab, .shell-space-group-sleeve'" in src
    assert "function measureSpaceDropLanes(" in src
    assert "function placeDraggedSpaceTab(" in src
    # The sleeve highlight matches the committed membership (no separate
    # hover rule since Phase 1).
    assert "is-drop-target" in src
    assert "spaceGroupSleeveAtPoint(" not in src
    mod = DROP_JS.read_text(encoding="utf-8")
    # The pill itself is never an insertion point: a sleeve lane resolves to
    # before its first visible member.
    assert "beforeSleeve" in mod
    assert "{ beforeTab: before.id } : { beforeSleeve: before.gid }" in mod


def test_sleeve_end_preview_matches_drop():
    # Aiming at the last slot parks the tab inside the sleeve end so the
    # preview agrees with the drop rule (same end slop on both sides).
    # Owner is spaces_drop.js (Phase 1); the shell places via `place`.
    mod = DROP_JS.read_text(encoding="utf-8")
    assert "inside the sleeve end" in mod
    assert "r.left + r.width + 6" in mod  # same +6 end slop as membership
    assert "place = { park: joinSleeve }" in mod
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "placeDraggedSpaceTab(host, tab, target)" in src
    assert "sleeve.appendChild(tab)" in src


def test_tab_drag_survives_iframes_and_off_strip_releases():
    src = SHELL_JS.read_text(encoding="utf-8")
    # Gesture events ride on window: the strip is small and content iframes
    # swallow events aimed at it.
    assert "window.addEventListener('pointermove', (e) => {" in src
    assert "window.addEventListener('pointerup', (e) => {" in src
    assert "window.addEventListener('pointercancel', (e) => {" in src
    # While active, content iframes lose hit-testing so the release lands.
    assert "is-reordering-spaces" in src
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert "body.is-reordering-spaces iframe" in css


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
    assert "CuttleSpaces.toggleGroupCollapsed(spacesState, gid" in src
    mod = STATE_JS.read_text(encoding="utf-8")
    assert "collapsed: !!g.collapsed" in mod  # persisted
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


def test_spaces_scripts_load_before_shell():
    html = (Path(__file__).resolve().parents[2] / "src" / "web" / "app_shell.html").read_text(encoding="utf-8")
    idx = {}
    for name in ("spaces_state.js", "spaces_groups.js", "spaces_order.js", "spaces_drop.js", "spaces_activity.js", "app_shell.js"):
        pos = html.find(name)
        assert pos >= 0, name + " not loaded by app_shell.html"
        idx[name] = pos
    for name in ("spaces_state.js", "spaces_groups.js", "spaces_order.js", "spaces_drop.js", "spaces_activity.js"):
        assert idx[name] < idx["app_shell.js"], name + " must load before app_shell.js"


def test_group_tint_css():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert "color-mix" in css
    assert ".shell-space-group-count" in css
    # Grouped tabs take the sleeve background; singles keep their own wash.
    assert ".shell-spaces-tabs > .shell-space-tab.has-accent" in css
    assert ".shell-space-group-sleeve > .shell-space-tab.has-accent" in css


def test_group_sleeve_and_active_only_underline():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "shell-space-group-sleeve" in src
    assert "closeSleeve" in src
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".shell-space-group-sleeve" in css
    assert ".shell-space-group-sleeve.is-drop-target" in css
    # Exactly one underline in the strip: the active tab. Dark gray fallback
    # when the active space has no color.
    assert ".shell-space-tab.is-active::before" in css
    assert "#6b7280" in css
    assert ".shell-space-tab.has-accent::before" not in css


def test_group_menu_css():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".shell-space-group" in css
    assert ".shell-space-tab.has-accent" in css
    assert ".shell-space-ctx" in css
    assert ".shell-group-bubble" in css
    assert ".shell-ctx-swatch" in css
