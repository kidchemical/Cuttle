"""Shared searchable-select component (js/shared/searchable_select.js).

Source-level guards: the component exists with its public API, every UI
surface that needs a large model/agent catalog adopts it, and each page
loads it before its consumers.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_JS = REPO_ROOT / "src" / "web" / "js" / "shared" / "searchable_select.js"
SHARED_CSS = REPO_ROOT / "src" / "web" / "css" / "searchable_select.css"

CONSUMERS = {
    "chat chip config": REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_chip_config.js",
    "settings model picker": REPO_ROOT / "src" / "web" / "js" / "settings" / "settings_model_picker.js",
    "settings background helpers": REPO_ROOT / "src" / "web" / "js" / "settings" / "settings_background_helpers.js",
    "router editor": REPO_ROOT / "src" / "web" / "js" / "router" / "router_editor.js",
}

PAGES = {
    "chat": REPO_ROOT / "src" / "web" / "chat_page.html",
    "settings": REPO_ROOT / "src" / "web" / "settings_page.html",
    "router": REPO_ROOT / "src" / "web" / "router_editor.html",
}


def test_component_exposes_the_expected_api_and_skin():
    src = SHARED_JS.read_text(encoding="utf-8")
    for token in (
        "function enhance",
        "CuttleSearchableSelect",
        "cuttle-combo-trigger",
        "cuttle-combo-search",
        "cuttle-combo-list",
        "MutationObserver",
    ):
        assert token in src, token
    css = SHARED_CSS.read_text(encoding="utf-8")
    for token in (
        ".cuttle-combo-trigger",
        ".cuttle-combo-panel",
        ".cuttle-combo-search",
        ".cuttle-combo-option",
    ):
        assert token in css, token


def test_every_catalog_dropdown_consumer_adopts_the_component():
    for name, path in CONSUMERS.items():
        src = path.read_text(encoding="utf-8")
        assert "CuttleSearchableSelect" in src, f"{name} must use the shared component"


def test_pages_load_the_component_before_their_consumers():
    for name, path in PAGES.items():
        html = path.read_text(encoding="utf-8")
        assert "/js/shared/searchable_select.js" in html, name
        assert "css/searchable_select.css" in html, name
        shared_at = html.index("/js/shared/searchable_select.js")
        consumer = {
            "chat": "/js/chat/chat_chip_config.js",
            "settings": "/js/settings/settings_model_picker.js",
            "router": "/js/router/router_editor.js",
        }[name]
        assert shared_at < html.index(consumer), name
