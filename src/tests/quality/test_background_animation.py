"""The page-wide ambient gradient must animate on the compositor.

Animating background-position repaints the full viewport every frame; inside
the shell that cost seconds of main-thread time per 15s on Apps/Jobs.
"""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "web" / "css" / "landing_page.css"


def _keyframes(css, name):
    m = re.search(r"@keyframes\s+" + name + r"\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", css)
    assert m, name
    return m.group(1)


def test_ambient_gradient_animates_transform_only():
    css = CSS.read_text(encoding="utf-8")
    frames = _keyframes(css, "gradient-animation")
    assert "transform" in frames
    assert "background-position" not in frames


def test_ambient_gradient_pauses_and_respects_reduced_motion():
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"html\.cuttle-bg-paused body::before\s*\{[^}]*animation-play-state:\s*paused", css)
    reduced = css[css.index("prefers-reduced-motion"):]
    assert re.search(r"body::before\s*\{[^}]*animation:\s*none", reduced)


CHAT_CSS = CSS.parent / "chat_page.css"
UI_BOOT = CSS.parents[1] / "js" / "shared" / "ui_boot.js"


def test_unread_glow_animates_opacity_only():
    """A box-shadow animation restyles and repaints on the main thread every
    frame; the unread dots did that in every pane, on screen or not."""
    css = CHAT_CSS.read_text(encoding="utf-8")
    frames = _keyframes(css, "chat-unread-glow")
    assert "opacity" in frames and "box-shadow" not in frames
    assert "chat-unread-error-glow" not in css
    for selector in (".history-section-unread-dot::after", ".history-unread-icon::after",
                     ".chat-session-unread-icon::after"):
        assert selector in css
        assert f"html.cuttle-bg-paused {selector}" in css


def test_closed_history_panel_does_not_animate():
    css = CHAT_CSS.read_text(encoding="utf-8")
    closed = r"\.chat-history-panel:not\(\.open\):not\(\.mobile-open\):not\(\.hist-slide\)"
    assert re.search(closed + r" \*,\s*" + closed + r" \*::before,\s*" + closed
                     + r" \*::after\s*\{[^}]*animation-play-state:\s*paused", css)


def test_pane_without_visible_area_pauses_like_a_hidden_window():
    boot = UI_BOOT.read_text(encoding="utf-8")
    assert "window.innerWidth === 0 || window.innerHeight === 0" in boot
    assert "applyBgPaused(shellHidden || paneHasNoArea())" in boot
    assert "window.addEventListener('resize', syncBgPaused" in boot
