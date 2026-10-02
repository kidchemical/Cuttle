"""Settings → Agents / Providers, and the OOBE wizard (frontend contract).

Both surfaces render from server payloads (``/api/agents``,
``/api/settings/completion-providers``, ``/api/wizard/status``) rather than
hardcoding anything. That is the whole point — adding an agent must be a folder
drop — so the tests below pin the two ways it can silently rot: a hardcoded
agent/provider id sneaking back into the page, and a status word that does not
match what the server actually sends.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "src" / "web" / "settings_page.html"
WIZARD = REPO_ROOT / "src" / "web" / "wizard_page.html"
CSS = REPO_ROOT / "src" / "web" / "css" / "settings_page.css"


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _wizard() -> str:
    return WIZARD.read_text(encoding="utf-8")


# ── no hardcoded inventory ────────────────────────────────────────────────

BUNDLED_AGENTS = sorted(
    p.name
    for p in (REPO_ROOT / "src" / "api" / "agent_harness" / "agents").iterdir()
    if p.is_dir() and (p / "manifest.yaml").exists()
)


def test_settings_page_hardcodes_no_agent_id():
    """A new agent folder must show up without a page edit."""
    html = _page()
    for agent_id in BUNDLED_AGENTS:
        assert f"'{agent_id}'" not in html, (
            f"'{agent_id}' is hardcoded in settings_page.html; the catalog "
            "renders it"
        )
        assert f'"{agent_id}"' not in html, agent_id


def test_settings_page_hardcodes_no_completion_provider_id():
    """Provider ids belong to the registry, not the markup."""
    html = _page()
    for provider_id in ("openai", "anthropic", "local"):
        assert f"value=\"{provider_id}\"" not in html, provider_id


def test_agent_list_is_rendered_from_the_catalog_endpoint():
    html = _page()
    assert "fetch('/api/agents')" in html
    assert "function renderAgentCliList" in html
    assert "function renderAgentDropInRoots" in html


def test_completion_providers_come_from_the_registry_endpoint():
    html = _page()
    assert "/api/settings/completion-providers" in html
    assert "function loadCompletionProviders" in html
    # Both verbs: the write is the whole point of the replacement control.
    assert "method: 'POST'" in html
    assert "function saveCompletionProviders" in html


# ── what the cards say ────────────────────────────────────────────────────


def test_status_words_match_the_server_fields():
    """Each card state is derived from available/ready/credential_present."""
    html = _page()
    block = re.search(r"function agentStatusChip\(agent\) \{.*?\n        \}", html, re.S)
    assert block, "agentStatusChip not found"
    body = block.group(0)
    assert "agent.available && agent.ready" in body
    assert "agent.credential_env" in body
    assert "agent.credential_present" in body


def test_missing_cli_shows_the_install_hint_and_ready_ones_stay_quiet():
    html = _page()
    block = re.search(r"function renderAgentCliList\(payload\) \{.*?\n        \}", html, re.S)
    assert block
    body = block.group(0)
    # Prose only when there is something to do — a ready agent gets one line.
    assert "if (!agent.available && agent.install_hint)" in body
    assert "agent.auth_command" in body


def test_drop_in_roots_are_listed_so_adding_an_agent_is_discoverable():
    html = _page()
    assert 'id="agentDropInRoots"' in html
    assert "root.path" in html


def test_page_explains_that_cuttle_does_not_install_agents():
    """OOBE honesty: an uninstalled CLI is the user's job."""
    html = _page()
    assert "does not install" in html.lower() or "never installs" in html.lower()


# ── layout ────────────────────────────────────────────────────────────────


def test_agent_cards_use_a_responsive_grid():
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"\.agent-cli-list\s*\{[^}]*grid-template-columns", css)
    assert re.search(r"\.agent-card\s*\{[^}]*display:\s*flex", css)
    assert re.search(r"@media \(max-width: 640px\)", css)


def test_agent_card_and_provider_chip_classes_all_exist_in_css():
    css = CSS.read_text(encoding="utf-8")
    for cls in (
        "agent-card-head", "agent-card-name", "agent-card-slash",
        "agent-card-meta", "agent-card-hint", "provider-chip", "provider-status",
        "provider-picker",
    ):
        assert f".{cls}" in css, cls


def test_video_list_rows_no_longer_squash():
    """A column flex container shrinks children unless told not to; the rows
    came out cropped mid-row instead of scrolling."""
    css = CSS.read_text(encoding="utf-8")
    row = re.search(r"\.video-list-item\s*\{([^}]*)\}", css)
    assert row, "no .video-list-item rule"
    assert "flex: 0 0 auto" in row.group(1), "rows must not shrink to fit max-height"
    container = re.search(r"\.video-list-container\s*\{([^}]*)\}", css)
    assert container
    body = container.group(1)
    assert "max-height" in body
    # tall enough that a normal playlist shows without a scrollbar
    px = re.search(r"max-height:\s*clamp\([^;]*?(\d+)px", body)
    assert px, "container should scale with the viewport"
    assert int(px.group(1)) >= 340


def test_mobile_tab_strip_scrolls_instead_of_squeezing_labels():
    """Same shrink trap as the playlist rows: without flex:0 0 auto a nowrap
    strip truncates every label to 'Prov' and scrolls nothing."""
    css = CSS.read_text(encoding="utf-8")
    blocks = re.findall(r"@media \(max-width: 640px\)\s*\{(.*?)\n\}", css, re.S)
    mobile = next((b for b in blocks if ".settings-tabs" in b), None)
    assert mobile, "no mobile media query for the tab strip"
    strip = re.search(r"\.settings-tabs\s*\{([^}]*)\}", mobile)
    assert strip and "overflow-x: auto" in strip.group(1)
    tab = re.search(r"\.settings-tab\s*\{([^}]*)\}", mobile)
    assert tab, "no .settings-tab rule in the mobile query"
    assert "flex: 0 0 auto" in tab.group(1)


# ── the two dead controls are gone ────────────────────────────────────────


def test_dead_cheap_completion_dropdowns_are_removed():
    """They wrote preferred_llm_model / preferred_tools_ollama_model, which
    nothing read; the replacement writes settings llm_complete does read."""
    html = _page()
    for gone in (
        'id="modelSelect"',
        'id="toolOllamaModelSelect"',
        "preferred_llm_model",
        "preferred_tools_ollama_model",
        "function saveModel(",
        "function saveToolModel(",
        "function populateOllamaModelsInSettings(",
        "function populateToolOllamaModelsInSettings(",
        "function syncModelSelectFromBackend(",
    ):
        assert gone not in html, f"{gone} is back — it was a dead control"


def test_orphaned_localstorage_keys_are_gone_too():
    html = _page()
    for key in ("selectedToolModel", "'selectedModel'"):
        assert key not in html, key


# ── wizard ────────────────────────────────────────────────────────────────


def test_wizard_renders_agents_and_no_retired_steps():
    html = _wizard()
    assert "/api/wizard/status" in html
    assert "function renderWizardAgents" in html
    assert "steps.agent_cli" in html
    assert "steps.completion_provider" in html
    # Retired with the graphs / replaced by the registry.
    assert "steps.default_pipeline" not in html
    assert "steps.api_keys" not in html


def test_wizard_escapes_agent_strings():
    """install_hint comes from a drop-in manifest and lands in innerHTML."""
    html = _wizard()
    assert "function esc(" in html
    block = re.search(r"function renderWizardAgents\(box, data\) \{.*?\n        \}", html, re.S)
    assert block
    body = block.group(0)
    # Every interpolated agent field goes through esc().
    for field in ("a.label", "a.slash || ('/' + a.id)", "badge", "hint"):
        assert f"esc({field})" in body, field


def test_wizard_links_deep_into_the_settings_tabs():
    html = _wizard()
    assert "/settings_page.html?tab=agents" in html
    assert "/settings_page.html?tab=providers" in html


def test_wizard_agents_cover_the_three_real_states():
    html = _wizard()
    for token in ("a.ready", "!a.available", "a.credential_env", "a.auth_command", "a.install_hint"):
        assert token in html, token