"""Wallpaper playlist rows in the Appearance tab (``settings_page.html``).

The rows are the part of Settings a user actually stares at while editing a
playlist, and they are rendered from JS rather than markup, so nothing else
covers them. These tests pin the layout contract that was asked for:

* the playlist control sits on its own line under its label, left aligned —
  it used to be shoved to the far right of a `space-between` row,
* play and remove are the same size (`.control-button` has an 80px `min-width`
  meant for labelled buttons, which made the "−" a slab),
* every row can show a title / author / thumbnail, and the row degrades to a
  filename plus a placeholder when the URL is not YouTube.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PAGE = REPO_ROOT / "src" / "web" / "settings_page.html"
CSS_SETTINGS = REPO_ROOT / "src" / "web" / "css" / "settings_page.css"
CSS_COMPACT = REPO_ROOT / "src" / "web" / "css" / "compact_page.css"


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _render_fn() -> str:
    match = re.search(
        r"function renderVideoBackgroundList\(urls\) \{.*?\n        \}", _page(), re.S
    )
    assert match, "renderVideoBackgroundList not found"
    return match.group(0)


# ── layout ────────────────────────────────────────────────────────────────


def test_playlist_control_stacks_under_its_label():
    html = _page()
    assert "setting-item--stack" in html, "playlist row is not marked as stacked"
    assert "setting-control--stack" in html
    css = CSS_SETTINGS.read_text(encoding="utf-8")
    # `display: block` (not flex space-between) is what stops the right-push.
    assert re.search(r"\.setting-item--stack\s*\{[^}]*display:\s*block", css)
    assert re.search(
        r"\.setting-control--stack\s*\{[^}]*flex-direction:\s*column", css
    )


def test_playlist_row_markup_has_no_inline_flex_override():
    """Inline `display:flex` on the setting-item would beat the class rule."""
    html = _page()
    for tag in re.findall(r'<div class="setting-item[^"]*"[^>]*>', html):
        assert "style=" not in tag, f"inline style on setting row: {tag}"


def test_play_and_remove_share_one_width_rule():
    css = CSS_SETTINGS.read_text(encoding="utf-8")
    rule = re.search(r"\.video-list-item \.video-row-btn\s*\{([^}]*)\}", css)
    assert rule, "no .video-row-btn sizing rule"
    body = rule.group(1)
    assert re.search(r"min-width:\s*(\d+)px", body)
    width = re.search(r"min-width:\s*(\d+)px", body).group(1)
    assert re.search(rf"width:\s*{width}px", body), "min-width and width disagree"
    # A fixed height keeps the two glyph buttons clickable targets.
    assert "min-height" in body


def test_compact_shell_keeps_both_buttons_the_same_size():
    compact = CSS_COMPACT.read_text(encoding="utf-8")
    rule = re.search(
        r"body\.compact-shell-page \.video-list-item \.video-row-btn\s*\{([^}]*)\}", compact
    )
    assert rule, "compact shell no longer sizes playlist row buttons"
    body = rule.group(1)
    assert re.search(r"min-width:\s*(\d+)px", body)
    width = re.search(r"min-width:\s*(\d+)px", body).group(1)
    assert re.search(rf"width:\s*{width}px", body)


def test_stale_video_play_btn_sizing_is_gone():
    """compact_page.css used to size only .video-play-btn (play, not remove)."""
    compact = CSS_COMPACT.read_text(encoding="utf-8")
    assert ".video-list-item .video-play-btn" not in compact


def test_readme_demo_selector_survives():
    """src/scripts/utilities/readme_gifs.py drives the wallpaper GIF by class."""
    fn = _render_fn()
    assert "video-play-btn" in fn, "README wallpaper demo selector was dropped"


# ── row structure ─────────────────────────────────────────────────────────


def test_row_renders_thumb_caption_field_and_actions():
    fn = _render_fn()
    for token in (
        "video-row-thumb",
        "video-row-caption",
        "video-row-title",
        "video-row-meta",
        "video-row-url",
        "video-row-field",
        "video-row-buttons",
    ):
        assert token in fn, f"row is missing .{token}"


def test_metadata_is_requested_per_row_and_memoized():
    html = _page()
    assert "/api/settings/video-metadata?url=" in html
    assert "_videoMetaCache" in html
    assert "_videoMetaPending" in html
    fn = _render_fn()
    assert "requestVideoMeta(url" in fn


def test_unsupported_source_falls_back_to_filename_without_a_thumbnail():
    html = _page()
    assert "describeVideoSource" in html
    fn = _render_fn()
    # Unsupported (non-YouTube) rows show the filename and the placeholder glyph.
    assert "if (!meta || !meta.supported)" in fn
    assert "captionTitle.textContent = title || describeVideoSource(url)" in fn


def test_dead_thumbnail_falls_back_instead_of_showing_a_broken_image():
    fn = _render_fn()
    assert "addEventListener('error'" in fn
    assert "removeAttribute('src')" in fn
    assert "is-empty" in fn
    css = CSS_SETTINGS.read_text(encoding="utf-8")
    assert re.search(r"\.video-row-thumb\.is-empty::after", css)
    assert re.search(r"\.video-row-thumb img:not\(\[src\]\)", css)


def test_row_stays_usable_when_metadata_is_slow_or_fails():
    html = _page()
    # The caption is painted before the request resolves, from the URL itself.
    fn = _render_fn()
    assert fn.index("describeVideoSource(url)") < fn.index("requestVideoMeta(url")
    assert ".catch(() => paint({ supported: false, title: '' }))" in html
    assert ".finally(() => _videoMetaPending.delete(key))" in html


def test_list_reader_still_finds_every_row_input():
    """getVideoBackgroundListFromUI reads .video-list-item input — the rows are
    rebuilt constantly, so a class rename would silently save an empty list."""
    html = _page()
    assert "container.querySelectorAll('.video-list-item input')" in html
    fn = _render_fn()
    assert "inp.type = 'text'" in fn