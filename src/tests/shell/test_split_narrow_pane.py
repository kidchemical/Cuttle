"""Narrow split panes: compact new-chat splash + collapse-in-place.

Source contracts (no browser needed):
- `chat_page.css` compresses the welcome splash at pane widths where the
  700px form would otherwise squeeze, and carries no base slide transition
  on the history panel (the 768px sheet-docking flip used to replay as a
  visible sweep on every iframe resize: pane drag, + button, rotate).
- `app_shell.js` parks a horizontally dragged pane released below
  SPLIT_COLLAPSE_PX via setPaneCollapsed — blade toolbar stays, live
  iframe untouched (never closeSplitColumn). The touching divider becomes
  a click-to-restore affordance; fluid panes share the budget around
  pinned panes; the collapsed flag persists in the layout tree.
- `app_shell.css` carries the parked-pane + expand-divider selectors.
- `chat_page.js` runs the panel slide only under .hist-slide around
  intentional open/close toggles.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CHAT_CSS = REPO / "src" / "web" / "css" / "chat_page.css"
CHAT_JS = REPO / "src" / "web" / "js" / "chat" / "chat_page.js"
SHELL_JS = REPO / "src" / "web" / "js" / "shell" / "app_shell.js"
SHELL_CSS = REPO / "src" / "web" / "css" / "app_shell.css"


def _media_block(css: str, query: str) -> str:
    head, _, tail = css.partition(query)
    assert tail, f"missing {query}"
    return head, tail[:4000]


def test_narrow_pane_compacts_welcome_splash():
    css = CHAT_CSS.read_text(encoding="utf-8")
    _, block = _media_block(css, "@media (max-width: 560px)")
    assert ".welcome-screen" in block
    assert ".welcome-logo" in block
    assert ".welcome-subtitle" in block
    assert ".welcome-input-container" in block
    # Two-across suggestion cards do not fit a narrow pane.
    assert ".suggestion-cards" in block
    assert "grid-template-columns: 1fr" in block


def test_welcome_composer_matches_in_chat_width():
    css = CHAT_CSS.read_text(encoding="utf-8")
    box = css.split(".welcome-input-container {", 1)[1].split("}", 1)[0]
    # In-chat parity (.input-wrapper 820px / 16px sides), still capped.
    assert "max-width: 820px" in box
    assert "padding: 0 16px" in box


def test_welcome_content_shrinks_with_narrow_panes():
    css = CHAT_CSS.read_text(encoding="utf-8")
    content = css.split(".welcome-content {", 1)[1].split("}", 1)[0]
    # Width-bound (never wider than its max) and shrinkable: the old
    # flex-shrink: 0 let the splash overflow panes as they narrowed.
    assert "width: 100%" in content
    assert "max-width: 860px" in content
    assert "min-width: 0" in content
    assert "box-sizing: border-box" in content
    assert "flex-shrink" not in content


def test_very_narrow_pane_drops_greeting():
    css = CHAT_CSS.read_text(encoding="utf-8")
    _, block = _media_block(css, "@media (max-width: 360px)")
    assert ".welcome-subtitle" in block
    assert "display: none" in block
    assert ".welcome-logo" in block


def test_sub_threshold_drag_parks_pane_in_place():
    js = SHELL_JS.read_text(encoding="utf-8")
    assert "SPLIT_COLLAPSE_PX = 200" in js
    assert "function setPaneCollapsed" in js
    assert "function isPaneCollapsedEl" in js
    collapse = js.split("function maybeCollapseNarrowPane", 1)[1][:1600]
    # A park, not a close: the pane (and its live iframe) must survive.
    assert "setPaneCollapsed" in collapse
    assert "closeSplitColumn" not in collapse
    # Parked panes stay pinned while fluid siblings share the budget.
    assert "'0 0 auto'" in js
    # The touching divider unparks first, then drags to a chosen width;
    # a pure click restores at equal shares, a small drag floors at 200px.
    assert "expandTargets" in js
    assert "maxDelta" in js
    assert "SPLIT_COLLAPSE_PX" in js
    # Blade icons unpark their pane before navigating.
    assert "isPaneCollapsedEl(column)) setPaneCollapsed(column, false)" in js
    assert "dataset.expand" in js
    assert "pane-will-collapse" in js
    assert "will-collapse" in js


def test_park_and_restore_leave_no_preview_crumbs():
    js = SHELL_JS.read_text(encoding="utf-8")
    collapse = js.split("function setPaneCollapsed", 1)[1][:1500]
    # The dim + red dash belong to an in-flight gesture only.
    assert "pane-will-collapse" in collapse
    assert "will-collapse" in collapse


def test_chevron_points_where_the_pane_grows():
    js = SHELL_JS.read_text(encoding="utf-8")
    # Parked-left grows rightward; parked-right grows leftward.
    assert "verticalHandles ? 'down' : 'right'" in js
    assert "verticalHandles ? 'up' : 'left'" in js
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert "data-expand-dir='right']::after" in css
    assert "data-expand-dir='left']::after" in css


def test_vertical_stacks_park_to_a_restore_bar():
    js = SHELL_JS.read_text(encoding="utf-8")
    # No orientation refusal left in the collapse path.
    assert "if (vertical || shrinkingPos" not in js
    assert "verticalHost" in js
    # The whole parked pane is the restore affordance there.
    assert ".split-vertical > .split-column.pane-collapsed" in js
    # The bar carries the live chat name, else the page title.
    assert "function parkedPaneTitle" in js
    assert "function refreshParkedPaneTitle" in js
    assert "chatSessionTitleText" in js
    assert "PAGE_TITLES[base]" in js
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".split-vertical > .split-column.pane-collapsed" in css
    assert "height: 28px" in css
    assert "attr(data-parked-title)" in css
    assert "data-expand-dir='down']::after" in css
    assert "data-expand-dir='up']::after" in css


def test_collapsed_flag_persists_across_reload():
    js = SHELL_JS.read_text(encoding="utf-8")
    assert "entry.collapsed = true" in js
    assert "node.collapsed" in js
    assert "{ quiet: true }" in js


def test_history_panel_slides_only_on_intentional_toggle():
    css = CHAT_CSS.read_text(encoding="utf-8")
    base = css.split(".chat-history-panel {", 1)[1].split("}", 1)[0]
    # No armed transition on the resting panel: breakpoint docking flips
    # (left sheet <-> right sheet) can never replay as a sweep.
    assert "transition" not in base
    slide = css.split(".chat-history-panel.hist-slide", 1)
    assert len(slide) == 2
    assert "transition" in slide[1][:200]
    assert "transform" in slide[1][:200]
    chat = CHAT_JS.read_text(encoding="utf-8")
    assert "animateHistoryPanelSlide" in chat
    assert "hist-slide" in chat
    # Both directions route through the helper.
    assert chat.count("animateHistoryPanelSlide(panel)") >= 2
    # The drag-time broadcast workaround is gone with the base transition.
    assert "cuttle-split-resize" not in chat
    shell = SHELL_JS.read_text(encoding="utf-8")
    assert "cuttle-split-resize" not in shell


def test_park_and_expand_have_styles():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".split-column.pane-collapsed" in css
    assert ".split-column.pane-collapsed .shell-main" in css
    assert ".split-resize-handle.pane-expand" in css
    assert ".split-resize-handle.will-collapse" in css
