"""Drag a blade grip onto a space tab: quick drop moves, 1s hover switches."""

from __future__ import annotations

from pathlib import Path

SHELL_JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "shell/app_shell.js"
SHELL_CSS = Path(__file__).resolve().parents[3] / "src" / "web" / "css" / "app_shell.css"


def test_hover_threshold_is_one_second():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "const PANE_SPACE_HOVER_MS = 1000" in src


def test_quick_drop_appends_far_right_horizontal():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "function appendLayoutLeafToEnd(" in src
    body = src.split("function appendLayoutLeafToEnd(", 1)[1].split(
        "/** Depth-first leaf lookup", 1
    )[0]
    assert "orientation: 'horizontal'" in body
    assert "root.children.push(node)" in body  # placed at end of the existing row
    assert "function moveDraggedPaneToSpace(" in src
    # Release point is hit-tested against the tab bar, not the last hover frame.
    assert "spaceTabAtPoint(upEvent.clientX" in src


def test_long_hover_switches_space_for_swap():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "PANE_SPACE_HOVER_MS" in src
    assert "switchSpace(spaceId)" in src
    assert "function exchangeCarriedPaneWithLive(" in src
    # The displaced pane's content travels back to the source space slot.
    assert "findLayoutLeafNode(src.root" in src


def test_single_pane_can_start_cross_space_drag():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "getSplitColumns().length < 2 && spacesState.spaces.length < 2" in src
    assert "has-cross-space" in src


def test_tab_hover_visuals():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".shell-space-tab.pane-space-hover" in css
    assert "space-hover-countdown" in css
    assert ".split-container.has-cross-space .rail-pane-grip" in css


def test_grip_tooltip_mentions_spaces():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "onto a space tab to move" in src
