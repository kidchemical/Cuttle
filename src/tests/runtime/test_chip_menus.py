"""Composer chip config modal: project (+branch) and agent (+model/effort).

Source-level + wiring guards. The modal DOM/IO, chip-row binding, and the
project-chip branch cache live in `chat_chip_config.js` (`CuttleChipConfig`);
the page owns chip state/setters and delegates. Behavior is covered by the
browser E2E suite.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_page.js"
MODULE_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_chip_config.js"
SHARED_JS = REPO_ROOT / "src" / "web" / "js" / "shared" / "searchable_select.js"
SHARED_CSS = REPO_ROOT / "src" / "web" / "css" / "searchable_select.css"
CHAT_CSS = REPO_ROOT / "src" / "web" / "css" / "chat_page.css"
CHAT_HTML = REPO_ROOT / "src" / "web" / "chat_page.html"


def test_chip_row_binding_is_wired_from_render_slash_chips():
    src = CHAT_JS.read_text(encoding="utf-8")
    render = src.split("function renderSlashChips", 1)[1].split("\n    function ", 1)[0]
    assert "chipConfigControl.bindRow(row, {" in render


def test_page_delegates_to_the_chip_config_module():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "CuttleChipConfig.create({" in src
    assert "CuttleChipConfig.branchFor(" in src
    assert "CuttleChipConfig.refreshBranch(" in src
    # The modal DOM + chip binding must NOT live in the page monolith.
    for leak in (
        "ensureChipConfigModal",
        "closeChipConfigModal",
        "openChipConfigModal",
        "chip-config-select",
        "bindChipClickMenus",
        "CHIP_AGENT_MENU_SPECS",
        "_projectBranchCache",
    ):
        assert leak not in src, f"{leak} leaked into chat_page.js"


def test_agent_menu_spec_stays_page_owned():
    src = CHAT_JS.read_text(encoding="utf-8")
    block = src.split("function agentMenuSpec", 1)[1].split("\n    }", 1)[0]
    for agent in ("cursor", "muse", "hermes", "opencode", "codex", "claude"):
        assert f"{agent}: {{" in block, agent


def test_module_owns_the_modal_and_branch_cache():
    src = MODULE_JS.read_text(encoding="utf-8")
    for fn in (
        "function create",
        "function openProject",
        "function openAgent",
        "function bindRow",
        "function fillSelect",
        "function loadBranches",
        "function switchBranch",
        "function stickyAgentEntries",
        "function agentSettingOptions",
        "function branchFor",
        "function refreshBranch",
    ):
        assert fn in src, fn


def test_page_loads_the_module_before_the_page():
    html = CHAT_HTML.read_text(encoding="utf-8")
    mod_at = html.index("/js/chat/chat_chip_config.js")
    page_at = html.index("/js/chat/chat_page.js")
    assert mod_at < page_at


def test_chip_modal_reuses_the_shared_auth_modal_skin():
    src = MODULE_JS.read_text(encoding="utf-8")
    assert "auth-modal-overlay" in src
    assert "auth-modal chip-config-modal" in src


def test_project_chip_advertises_the_modal():
    src = CHAT_JS.read_text(encoding="utf-8")
    chip = src.split("function projectChipHtml", 1)[1].split("\n    }", 1)[0]
    assert 'aria-haspopup="true"' in chip
    assert 'aria-expanded="false"' in chip


def test_chip_modal_css_exists():
    # Shared by chat, the shell and app pages (css/chip_config_modal.css).
    css = (CHAT_CSS.parent / "chip_config_modal.css").read_text(encoding="utf-8")
    page = (CHAT_CSS.parent.parent / "chat_page.html").read_text(encoding="utf-8")
    assert "chip_config_modal.css" in page
    assert ".chip-config-modal" in css
    assert ".chip-config-select" in css
    assert ".chip-config-field-label" in css


def test_chip_modal_dropdowns_use_the_shared_searchable_select():
    # The chip modal delegates to the shared component; it does not own a
    # private combobox implementation.
    mod = MODULE_JS.read_text(encoding="utf-8")
    assert "CuttleSearchableSelect.enhance" in mod
    assert "function enhanceSelects" in mod
    for leak in ("chip-combo", "function initCombos", "function buildComboList"):
        assert leak not in mod, f"{leak} should live in the shared component"

    shared = SHARED_JS.read_text(encoding="utf-8")
    for token in (
        "function enhance",
        "cuttle-combo-trigger",
        "cuttle-combo-search",
        "cuttle-combo-option",
        "MutationObserver",
    ):
        assert token in shared, token

    css = SHARED_CSS.read_text(encoding="utf-8")
    for token in (
        ".cuttle-combo-trigger",
        ".cuttle-combo-panel",
        ".cuttle-combo-search",
        ".cuttle-combo-option",
    ):
        assert token in css, token


def test_chat_page_loads_the_shared_select_before_the_chip_modal():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert "/js/shared/searchable_select.js" in html
    assert "css/searchable_select.css" in html
    shared_at = html.index("/js/shared/searchable_select.js")
    mod_at = html.index("/js/chat/chat_chip_config.js")
    assert shared_at < mod_at
