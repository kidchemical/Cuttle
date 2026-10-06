"""Chat palette must not surface agent-router config commands.

Router tuning belongs on the Router page (`router_editor.html`). Chat keeps
sticky agent slash commands (/cursor, /codex, …) plus per-agent model picks
(/model … for Cursor, /muse model … for Muse).
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
CHAT_JS = WEB / "js" / "chat/chat_page.js"
SLASH_JS = WEB / "js" / "chat/chat_slash.js"
CHAT_HTML = WEB / "chat_page.html"

ROUTER_PALETTE_PREFIXES = ("/router ", "/route ", "/retry ")


def _slash_commands_block(js: str) -> str:
    # Registry lives in chat_slash.js since Phase 3 Slice 2.
    start = js.find("const SLASH_COMMANDS = [")
    assert start >= 0, "SLASH_COMMANDS array not found in chat_slash.js"
    end = js.find("const CURSOR_AGENT_SLASH_COMMANDS", start)
    assert end > start, "CURSOR_AGENT_SLASH_COMMANDS marker missing after SLASH_COMMANDS"
    return js[start:end]


def test_router_slash_commands_not_in_chat_palette():
    js = SLASH_JS.read_text(encoding="utf-8")
    block = _slash_commands_block(js)
    for prefix in ROUTER_PALETTE_PREFIXES:
        assert f"prefix: '{prefix}'" not in block and f'prefix: "{prefix}"' not in block, (
            f"Router command {prefix!r} must not appear in SLASH_COMMANDS"
        )


def test_no_parameterized_router_palette_builder():
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "function buildRouterPaletteItems(" not in js
    assert "loadRouterOptionsForPalette" not in js
    filt_start = js.find("function filterSlashPaletteItems(")
    assert filt_start > 0
    filt = js[filt_start : filt_start + 3000]
    assert "buildRouterPaletteItems" not in filt
    assert "routerCmds" not in filt


def test_sticky_agent_commands_remain():
    js = SLASH_JS.read_text(encoding="utf-8")
    block = _slash_commands_block(js)
    for prefix in ("/cursor ", "/codex ", "/muse ", "/claude "):
        assert f"prefix: '{prefix}'" in block, f"Missing sticky agent {prefix!r}"
        entry = block.split(f"prefix: '{prefix}'", 1)[1].split("},", 1)[0]
        assert "stickySession: true" in entry, f"{prefix} lost stickySession"


def test_cursor_model_palette_still_wired():
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "function buildCursorModelPaletteItems(" in js
    filt_start = js.find("function filterSlashPaletteItems(")
    filt = js[filt_start : filt_start + 3000]
    assert "buildCursorModelPaletteItems(f)" in filt


def test_cursor_usage_palette_gated_to_cursor_chip():
    """`/usage` is a Cursor Agent nested slash — only when the Cursor chip is on."""
    js = CHAT_JS.read_text(encoding="utf-8")
    slash_js = SLASH_JS.read_text(encoding="utf-8")
    start = slash_js.find("const CURSOR_AGENT_SLASH_COMMANDS = [")
    end = slash_js.find("];", start)
    assert start > 0 and end > start
    block = slash_js[start:end]
    assert "prefix: '/usage'" in block
    assert "label: 'Usage'" in block

    # Must not appear in the global sticky/agent list.
    base = _slash_commands_block(slash_js)
    assert "prefix: '/usage'" not in base

    gate = js.find("function cursorAgentSlashCommandsForPalette(")
    assert gate > 0
    body = js[gate : gate + 500]
    assert "hasActiveCursorAgentChip()" in body
    assert "CURSOR_AGENT_SLASH_COMMANDS" in body


def test_harness_usage_palette_gated_per_agent():
    """Muse/Codex/Hermes/OpenCode/Claude each get a chip-gated `/usage` row."""
    js = CHAT_JS.read_text(encoding="utf-8")
    slash_js = SLASH_JS.read_text(encoding="utf-8")
    assert "HARNESS_USAGE_SLASH_BY_AGENT" in slash_js
    assert "function harnessUsageSlashCommandsForPalette(" in js
    for cat in ("muse-cmd", "codex-cmd", "hermes-cmd", "opencode-cmd", "claude-cmd"):
        assert f"category: '{cat}'" in slash_js
    base = _slash_commands_block(slash_js)
    assert "prefix: '/usage'" not in base


def test_agent_router_options_endpoint_requires_owner():
    """Owner-only: payload carries live `current` provider config.

    Palette no longer calls it; the Router page sends the owner cookie.
    Owner-200 + payload shape is covered in test_http_authz.py.
    """
    from api import web_chat_api as w

    with w.app.test_client() as client:
        anon = client.get(
            "/api/agent-router/options",
            environ_base={"REMOTE_ADDR": "192.0.2.77"},
        )
        assert anon.status_code == 401
        loop = client.get(
            "/api/agent-router/options",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert loop.status_code == 401


def test_chat_page_html_cache_buster_present():
    html = CHAT_HTML.read_text(encoding="utf-8")
    m = re.search(r'src="/js/chat/chat_page\.js\?v=([A-Za-z0-9]+)"', html)
    assert m, "chat_page.html must load /js/chat/chat_page.js with a ?v= cache-buster"
    assert m.group(1), "cache-buster query must be non-empty"
