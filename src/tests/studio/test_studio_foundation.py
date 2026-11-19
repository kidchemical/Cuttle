"""Studio foundation (L0): Display row wiring, boot gate, drift checks, theme-derived tokens."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Settings Display row and wiring --------------------------------------

def test_settings_page_has_the_hidden_display_row_and_scripts():
    html = _read(WEB / "settings_page.html")
    assert re.search(r'id="chatDisplayRow"\s+hidden', html)
    assert 'id="chatDisplaySelect"' in html
    assert '<option value="classic">Classic</option>' in html
    assert '<option value="studio">Studio</option>' in html
    assert html.index('/js/shared/chat_display.js') < html.index('settings_chat_preferences.js')
    assert "chatPreferences.bindChatDisplay(" in html
    assert "CuttleChatDisplay.syncFlag()" in html


def test_settings_owner_exports_bind_chat_display():
    src = _read(WEB / "js" / "settings" / "settings_chat_preferences.js")
    assert re.search(r"return \{[^}]*\bbindChatDisplay\b[^}]*\};", src)


# --- boot gate and drift checks -------------------------------------------

def test_ui_boot_gates_studio_on_pref_and_cached_flag_and_forces_bubbles():
    src = _read(WEB / "js" / "shared" / "ui_boot.js")
    assert "read(CHAT_DISPLAY_KEY) === 'studio' && read(CHAT_STUDIO_FLAG_KEY) === '1'" in src
    assert "root.classList.toggle('cuttle-studio', studio);" in src
    assert "root.classList.toggle('cuttle-flat-messages', !studio && read(CHAT_BUBBLES_KEY) === '0');" in src


def test_boot_and_helper_share_the_same_storage_keys():
    boot = _read(WEB / "js" / "shared" / "ui_boot.js")
    helper = _read(WEB / "js" / "shared" / "chat_display.js")
    for key in ("cuttleChatDisplay", "cuttleChatStudioFlag"):
        assert f"'{key}'" in boot and f"'{key}'" in helper, key
    assert "'cuttle-studio'" in helper


def _set_literal(src: str, name: str) -> list[str]:
    m = re.search(rf"const {name} = \[(.*?)\];", src, re.S)
    assert m, name
    return re.findall(r"'([^']+)'", m.group(1))


def test_appearance_relay_set_matches_the_peek_drawer():
    helper = _read(WEB / "js" / "shared" / "chat_display.js")
    peek = _read(WEB / "js" / "chat" / "chat_subagent_peek.js")
    m = re.search(r"const APPEARANCE = new Set\(\[(.*?)\]\);", peek, re.S)
    assert m
    peek_set = re.findall(r"'([^']+)'", m.group(1))
    assert _set_literal(helper, "APPEARANCE") == peek_set


# --- tokens ---------------------------------------------------------------

TOKENS = WEB / "css" / "studio" / "studio_tokens.css"


def test_tokens_are_scoped_to_the_studio_class():
    css = _read(TOKENS)
    for line in css.splitlines():
        stripped = line.strip()
        if not stripped.endswith("{") or stripped.startswith("@") or stripped.startswith("/*"):
            continue
        assert stripped.startswith(":root.cuttle-studio"), line
    assert not re.search(r"^:root\s*[{,]", css, re.M)


def test_tokens_derive_colours_from_the_active_theme():
    """Studio is a shape layer over the theme: its colours come from theme variables."""
    css = _read(TOKENS)
    assert not re.search(r"#[0-9a-fA-F]{6}\b", css), "fixed colour in studio_tokens.css"
    for var in ("--accent-color", "--accent-hover", "--text-primary", "--text-secondary",
                "--message-user-bg", "--message-assistant-bg", "--bubble-shadow"):
        assert f"var({var}" in css, var


def test_studio_sets_no_font_so_theme_and_text_prefs_apply():
    """No Studio stylesheet picks a typeface or bundles one (theme fonts and the
    Text & fonts prefs own type). A monospace stack for code-like text is the one exception."""
    for path in sorted((WEB / "css" / "studio").glob("*.css")):
        css = _read(path)
        assert "@font-face" not in css, path.name
        assert "--font-sans:" not in css, path.name
        for line in css.splitlines():
            if "font-family:" in line:
                assert "monospace" in line, f"{path.name}: {line.strip()}"
    assert not (WEB / "css" / "fonts" / "inter").exists()


def test_studio_stylesheets_keep_fixed_colours_to_status_tones():
    """Outside the semantic status tones (warn/bad/ok/info), Studio CSS uses theme-derived tokens."""
    allowed = {
        "supervision.css": ("--sup-tone-",),
        "work_panel.css": ("is-failed", "is-warn", "is-danger"),
    }
    for path in sorted((WEB / "css" / "studio").glob("*.css")):
        css = re.sub(r"/\*.*?\*/", "", _read(path), flags=re.S)
        for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            selector, body = block.group(1), block.group(2)
            for decl in body.split(";"):
                if not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", decl):
                    continue
                if "mask-image" in decl:
                    continue
                marks = allowed.get(path.name, ())
                assert any(m in decl or m in selector for m in marks), f"{path.name}: {decl.strip()}"


def test_no_runtime_font_cdn_in_studio_assets():
    for path in (TOKENS, WEB / "js" / "shared" / "chat_display.js", WEB / "js" / "shared" / "agent_identity.js"):
        assert "fonts.googleapis" not in _read(path), path.name
        assert "fonts.gstatic" not in _read(path), path.name
