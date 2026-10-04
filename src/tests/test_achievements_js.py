"""Client-side achievements slices: pure logic + shell wiring.

Two halves:

* node harness (skipped without node) exercising the pure helpers exported by
  ``src/web/js/achievements.js`` and ``src/web/js/celebrate.js``,
* static wiring assertions — load order, no polling on the settings page, and
  the Electron allowlist for native SFX.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
JS_DIR = REPO / "src" / "web" / "js"
SHELL_HTML = REPO / "src" / "web" / "app_shell.html"
SETTINGS_HTML = REPO / "src" / "web" / "settings_page.html"

HARNESS = r"""
const path = process.env.JS_DIR;
const A = require(path + '/achievements.js');
const C = require(path + '/celebrate.js');
const out = {};

// --- ordering / filtering -------------------------------------------------
out.orderNewestFirst = A.orderForCelebration([
    { id: 'a', unlocked_at: 10 },
    { id: 'b', unlocked_at: 30 },
    { id: 'c', unlocked_at: 20 },
]).map(i => i.id);
out.celebratableDropsSeen = A.celebratable([
    { id: 'a' }, { id: 'b', seen: true }, { id: 'c', seen: false }, null,
]).map(i => i.id);
out.batches = A.batch([1, 2, 3, 4, 5, 6, 7].map(n => ({ id: 'n' + n, unlocked_at: n })), 3)
    .map(w => w.map(i => i.id));

// --- formatting -----------------------------------------------------------
out.format = [A.formatCount(999), A.formatCount(1500), A.formatCount(12345),
              A.formatCount(1234567), A.formatCount(123456789),
              A.formatCount(1234567890123)];
out.percent = [A.percentOf({ progress: 50, threshold: 200 }),
               A.percentOf({ progress: 500, threshold: 200 }),
               A.percentOf({ progress: 5, threshold: 0 })];

// --- grouping -------------------------------------------------------------
const items = [
    { id: 'x', category: 'tokens', rarity: 'common', title: 'X' },
    { id: 'y', category: 'tokens', rarity: 'legendary', title: 'Y' },
    { id: 'z', category: 'milestones', rarity: 'common', title: 'Z' },
    { id: 'w', category: 'brand-new', rarity: 'epic', title: 'W' },
];
out.groups = A.groupByCategory(items).map(g => ({
    key: g.key,
    order: g.items.map(i => i.id),
}));

// --- celebration tiers ----------------------------------------------------
out.tiers = Object.keys(C.RARITY_TIERS).map(r => [r, C.tierFor(r).confetti, C.tierFor(r).sfx]);
out.unknownTier = C.tierFor('nonsense').confetti;
out.colors = ['common', 'rare', 'epic', 'legendary', 'mythic'].map(C.rarityColor);
out.progressLine = [
    C.progressLine({ unlocked: false, progress: 5, threshold: 10 }, A.formatCount),
    C.progressLine({ unlocked: true, progress: 10, threshold: 10 }, A.formatCount),
];

console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pure_slice_helpers():
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True,
        text=True,
        env={"JS_DIR": str(JS_DIR), "PATH": "/usr/bin:/bin"},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)

    # Newest unlock first, so a burst reads newest → oldest.
    assert got["orderNewestFirst"] == ["b", "c", "a"]
    assert got["celebratableDropsSeen"] == ["a", "c"]
    assert got["batches"] == [["n7", "n6", "n5"], ["n4", "n3", "n2"], ["n1"]]

    assert got["format"] == ["999", "1.5k", "12k", "1.2M", "123M", "1235B"]
    assert got["percent"] == [25.0, 100.0, 0.0]

    # Catalog order, unknown categories last, rarity-ascending within a group.
    assert [g["key"] for g in got["groups"]] == ["milestones", "tokens", "brand-new"]
    assert got["groups"][1]["order"] == ["x", "y"]   # common before legendary
    assert got["groups"][0]["order"] == ["z"]

    # Rarity drives celebration intensity: confetti only from epic upward.
    assert got["tiers"] == [
        ["common", 0, False], ["rare", 0, True], ["epic", 60, True],
        ["legendary", 120, True], ["mythic", 200, True],
    ]
    assert got["unknownTier"] == 0
    assert len(set(got["colors"])) == 5
    assert got["progressLine"] == ["Progress 5 / 10", ""]


def test_slices_load_before_app_shell():
    """app_shell composes; the slices must be in place before it runs."""
    html = SHELL_HTML.read_text(encoding="utf-8")
    order = {name: html.find(name) for name in
             ("celebrate.js", "achievements.js", "app_shell.js")}
    for name, pos in order.items():
        assert pos >= 0, f"{name} not loaded by app_shell.html"
    assert order["celebrate.js"] < order["app_shell.js"]
    assert order["achievements.js"] < order["app_shell.js"]


def test_settings_page_only_manages_flags():
    html = SETTINGS_HTML.read_text(encoding="utf-8")
    assert "achievements.js" not in html
    assert "achievementsGrid" not in html
    assert "rescanAchievements" not in html
    assert "loadExperimentalFlags" in html
    assert "tabs.register('experimental'" in html


def test_trophy_case_is_a_discoverable_app():
    html = (REPO / "src/web/achievements_page.html").read_text()
    assert 'id="achievementsGrid"' in html
    assert '__CUTTLE_ACHIEVEMENTS_MANUAL = true' in html
    assert 'achievements_page.js' in html
    shell = SHELL_HTML.read_text()
    assert 'data-page="/achievements_page.html"' in shell
    assert 'data-id="nav-achievements"' in shell


def test_settings_page_has_the_experimental_tab():
    html = SETTINGS_HTML.read_text(encoding="utf-8")
    assert 'data-tab="experimental"' in html
    assert 'id="panel-experimental"' in html
    assert 'aria-controls="panel-experimental"' in html


def test_toast_module_supports_achievement_cards():
    src = (JS_DIR / "toast.js").read_text(encoding="utf-8")
    assert "options.achievement" in src
    assert "options.duration" in src
    assert "cuttle-toast-achievement" in src


def test_chime_asset_exists_and_is_copied_for_electron():
    web_wav = REPO / "src" / "web" / "sounds" / "achievement-unlock.wav"
    electron_wav = REPO / "electron" / "assets" / "achievement-unlock.wav"
    assert web_wav.exists() and web_wav.stat().st_size > 1000
    # electron/assets is checked in, not generated — the copies must match.
    assert electron_wav.exists()
    assert electron_wav.read_bytes() == web_wav.read_bytes()


def test_electron_sfx_playback_is_allowlisted():
    main_js = (REPO / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (REPO / "electron" / "preload.js").read_text(encoding="utf-8")
    assert "NATIVE_SFX_FILES" in main_js
    assert "'achievement-unlock': 'achievement-unlock.wav'" in main_js
    assert "ipcMain.on('play-sfx'" in main_js
    # The renderer may only pass a name, never a path.
    assert "resolveSfxWavPath(name)" in main_js
    assert "playSfx:" in preload