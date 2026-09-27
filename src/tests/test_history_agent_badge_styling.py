"""History-panel agent badges must use the same harness chrome as in-chat."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


def test_frontend_wires_unified_history_badge_path():
    text = CHAT_JS.read_text(encoding="utf-8")
    assert "resolveSlashChipPaletteCategory" in text
    assert "normalizeAgentSlashChips" in text
    assert "normalizeAgentSlashChips(chips, sessionId)" in text
    assert "const useLive = isCurrentHistorySession(sessionId)" in text
    assert "stickyTitleChipsForSession(sessionId)" in text
    assert "slash-command-chip--palette-muse" in text
    # Muse/Hermes/OpenCode sticky agents are first-class harness categories.
    assert "category: 'muse'" in text
    assert "category: 'hermes'" in text
    assert "category: 'opencode'" in text
    assert "cat === 'muse' || cat === 'muse-model' || cat === 'muse-effort'" in text


@node_only
def test_resolve_slash_chip_palette_category_maps_legacy_muse_command():
    src = CHAT_JS.read_text(encoding="utf-8")
    composer = _extract(
        src,
        "    /** Agent id owning a composer chip, or '' for agent-less chips. Pure. */",
        "    /**\n     * Indexes to delete when the × on chips[idx] is clicked.",
    )
    resolve = _extract(
        src,
        "    /**\n     * Resolve the palette category used for chip chrome.",
        "    /** Drop \" - model\" (or any hyphen suffix) for compact chip labels. */",
    )
    category_class = _extract(
        src,
        "    /**\n     * Palette color class for a chip. Agent harness badges",
        "    /**\n     * Resolve the palette category used for chip chrome.",
    )
    harness = f"""
{composer}
{category_class}
{resolve}
const cases = {{
  legacyMuse: resolveSlashChipPaletteCategory({{
    label: 'Muse Code', meta: '/muse', category: 'command', prefix: '/muse ',
  }}),
  museCat: resolveSlashChipPaletteCategory({{
    label: 'Muse Code - Spark 1.3 · high', meta: '/muse', category: 'muse',
  }}),
  cursor: resolveSlashChipPaletteCategory({{
    label: 'Cursor - Auto', meta: '/cursor', category: 'cursor',
  }}),
  command: resolveSlashChipPaletteCategory({{
    label: 'Help', meta: '/help', category: 'command',
  }}),
  museClass: slashCommandChipCategoryClass('muse'),
  legacyClass: slashCommandChipCategoryClass(
    resolveSlashChipPaletteCategory({{
      label: 'Muse Code', meta: '/muse', category: 'command', prefix: '/muse ',
    }})
  ),
  cursorClass: slashCommandChipCategoryClass('cursor'),
}};
process.stdout.write(JSON.stringify(cases));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["legacyMuse"] == "muse", res
    assert res["museCat"] == "muse", res
    assert res["cursor"] == "cursor", res
    assert res["command"] == "command", res
    assert "palette-muse" in res["museClass"]
    assert "palette-muse" in res["legacyClass"]
    assert "palette-cursor" in res["cursorClass"]
    assert res["museClass"] == res["cursorClass"] or (
        "palette-muse" in res["museClass"] and "palette-cursor" in res["cursorClass"]
    )


@node_only
def test_history_chip_html_uses_muse_palette_for_legacy_command_category():
    src = CHAT_JS.read_text(encoding="utf-8")
    escape = (
        "function escapeHtmlInline(s) {\n"
        "  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
        ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');\n}\n"
    )
    composer = _extract(
        src,
        "    /** Agent id owning a composer chip, or '' for agent-less chips. Pure. */",
        "    /**\n     * Indexes to delete when the × on chips[idx] is clicked.",
    )
    category_class = _extract(
        src,
        "    /**\n     * Palette color class for a chip. Agent harness badges",
        "    /**\n     * Resolve the palette category used for chip chrome.",
    )
    resolve = _extract(
        src,
        "    /**\n     * Resolve the palette category used for chip chrome.",
        "    /** Drop \" - model\" (or any hyphen suffix) for compact chip labels. */",
    )
    parse = _extract(
        src,
        "    /**\n     * Split an agent badge label into harness / model / effort",
        "    /** History/header chips with model·effort must not hyphen-chop",
    )
    history_html = _extract(
        src,
        "    function slashCommandChipHistoryHtml(label, metaPrefix, category) {",
        "    const SLASH_CHIP_ERR_ICON =",
    )
    harness = f"""
{escape}
{composer}
{category_class}
{resolve}
{parse}
function slashChipShouldNotShorten(label) {{
  return /^(Muse Code|Cursor|Hermes|OpenCode)\\b/i.test(String(label || ''));
}}
{history_html}
const muse = slashCommandChipHistoryHtml(
  'Muse Code - Spark 1.3 · high', '/muse', 'command'
);
const cursor = slashCommandChipHistoryHtml('Cursor - Auto', '/cursor', 'cursor');
process.stdout.write(JSON.stringify({{
  museHasPalette: muse.includes('slash-command-chip--palette-muse'),
  museSegmented: muse.includes('slash-command-chip--segmented'),
  museHasSeg: muse.includes('slash-chip-seg--model'),
  cursorHasPalette: cursor.includes('slash-command-chip--palette-cursor'),
  cursorSegmented: cursor.includes('slash-command-chip--segmented'),
}}));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["museHasPalette"] is True, res
    assert res["museSegmented"] is True, res
    assert res["museHasSeg"] is True, res
    assert res["cursorHasPalette"] is True, res
    assert res["cursorSegmented"] is True, res
