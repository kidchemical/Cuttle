"""Studio foundation (L0): Display row wiring, boot gate, drift checks, tokens, fonts."""

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


def test_tokens_define_the_studio_font_and_accent():
    css = _read(TOKENS)
    assert "--font-sans: var(--studio-font-sans);" in css
    assert "'Cuttle Inter'" in css
    assert "#7048e8" in css
    # Classic keeps the global family name; Studio must not reuse it as @font-face.
    assert "font-family: 'Inter'" not in css


def test_tokens_font_urls_resolve_and_fonts_are_woff2():
    css = _read(TOKENS)
    urls = re.findall(r'url\("([^"]+\.woff2)"\)', css)
    assert len(urls) == 5
    for url in urls:
        target = (TOKENS.parent / url).resolve()
        assert target.is_file(), url
        assert target.read_bytes()[:4] == b"wOF2", url


def test_inter_weights_and_licence_are_vendored():
    fonts = WEB / "css" / "fonts" / "inter"
    for weight in (400, 500, 600, 700, 800):
        assert (fonts / f"inter-latin-{weight}-normal.woff2").is_file(), weight
    licence = _read(fonts / "LICENSE-OFL.txt")
    assert "SIL OPEN FONT LICENSE" in licence.upper()
    assert "Inter Project Authors" in licence
    assert (fonts / "README.md").is_file()


def test_no_runtime_font_cdn_in_studio_assets():
    for path in (TOKENS, WEB / "js" / "shared" / "chat_display.js", WEB / "js" / "shared" / "agent_identity.js"):
        assert "fonts.googleapis" not in _read(path), path.name
        assert "fonts.gstatic" not in _read(path), path.name
