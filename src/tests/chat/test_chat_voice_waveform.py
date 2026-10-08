"""Voice waveform: pure render math plus overlay markup contract.

Contracts pinned here:
- smoothLevel/mapLevel/ringRadii/barHeights are pure and bounded;
- the overlay carries a subtle background canvas, a ring canvas around the
  orbit, and a session-total strip (hidden until usage is known);
- the mic caption stays minimal ("Tap or hold to record");
- the waveform script loads before chat_voice.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
CHAT_JS = WEB / "js" / "chat"
WAVEFORM_JS = CHAT_JS / "chat_voice_waveform.js"
USAGE_JS = CHAT_JS / "chat_usage.js"
CHAT_HTML = WEB / "chat_page.html"
VOICE_CSS = WEB / "css" / "chat_voice.css"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


@node_only
def test_waveform_math_is_bounded_and_responsive():
    proc = subprocess.run(
        ["node", "-e", r"""
const assert = require('assert');
const W = require(process.argv[1]);
// Smoothing eases toward the target from both sides.
assert.ok(W.smoothLevel(0, 1) > 0 && W.smoothLevel(0, 1) < 1);
assert.ok(W.smoothLevel(1, 0) < 1 && W.smoothLevel(1, 0) > 0);
assert.strictEqual(W.smoothLevel(0.5, 0.5), 0.5);
// Levels map into 0..1 and grow with RMS.
assert.strictEqual(W.mapLevel(0), 0);
assert.strictEqual(W.mapLevel(-3), 0);
assert.ok(W.mapLevel(0.02) < W.mapLevel(0.2));
assert.ok(W.mapLevel(10) <= 1);
// Ring radii and bar heights are bounded fractions.
for (const t of [0, 1.7]) {
  for (const r of W.ringRadii(56, 1, t)) assert.ok(r >= 0 && r < 0.6, r);
  for (const h of W.barHeights(64, 1, t)) assert.ok(h >= 0 && h < 0.3, h);
  for (const h of W.barHeights(64, 0, t)) assert.ok(h < 0.05, h);
}
assert.strictEqual(W.ringRadii(0, 1, 0).length, 48, 'clamps to a sane default');
""", str(WAVEFORM_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_waveform_markup_hint_and_styles():
    html = CHAT_HTML.read_text(encoding="utf-8")
    overlay_at = html.find('id="voiceModeOverlay"')
    assert overlay_at >= 0
    bg_at = html.find('id="voiceModeWaveBg"')
    ring_at = html.find('id="voiceModeWaveRing"')
    assert bg_at > overlay_at, "subtle background waveform canvas"
    assert ring_at > overlay_at, "apparent ring canvas around the orbit"
    assert html.find('id="voiceModeSessionUsage"') > overlay_at
    hint_at = html.find('id="voiceModeHint"')
    hint_close = html.find('</p>', hint_at)
    hint_text = html[html.find('>', hint_at) + 1:hint_close]
    assert hint_text.strip() == "Tap or hold to record"
    order = [
        html.find('src="/js/chat/chat_voice_recorder.js'),
        html.find('src="/js/chat/chat_voice_waveform.js'),
        html.find('src="/js/chat/chat_voice.js'),
        html.find('src="/js/chat/chat_page.js'),
    ]
    assert all(i >= 0 for i in order) and order == sorted(order)
    css = VOICE_CSS.read_text(encoding="utf-8")
    for sel in (".voice-mode-wave-bg", ".voice-mode-wave-ring",
                ".voice-mode-session-usage", ".voice-mode-line-usage"):
        assert sel in css
