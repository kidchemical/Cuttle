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
