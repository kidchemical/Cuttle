"""Ensure ``/usage`` works for every Cuttle agent CLI harness that supports it.

Coverage matrix
---------------
* **Cursor** — ``handle_cursor_agent_slash`` / Cursor adapter ``handle_meta``
* **Muse / Codex / Hermes / OpenCode** — adapter ``handle_meta`` via
  ``api.agent_usage.handle_agent_usage_slash``
* Shared ``<cuttle_meters>`` contract (same bar UI Cursor ``/usage`` uses)
* Palette gating (chip-scoped ``/usage``, never a global sticky command)
* Agents without a usage reporter must not claim ``/usage`` via the shared helper

No live network / CLI calls — fetchers and CLI runners are mocked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from api.agent_usage import (
    cuttle_meters_markdown,
    format_codex_usage_markdown,
    format_hermes_usage_markdown,
    format_muse_usage_markdown,
    format_opencode_usage_markdown,
    handle_agent_usage_slash,
    parse_hermes_insights_text,
    parse_opencode_stats_text,
    parse_usage_slash,
)
from api.cursor_agent_commands import (
    format_cursor_usage_markdown,
    handle_cursor_agent_slash,
)

WEB = Path(__file__).resolve().parents[1] / "web"
CHAT_JS = WEB / "js" / "chat_page.js"
SLASH_JS = WEB / "js" / "chat_slash.js"
CHAT_CSS = WEB / "css" / "chat_page.css"

# Harnesses that expose a nested ``/usage`` one-shot in Cuttle chat.
USAGE_HARNESS_IDS = ("cursor", "muse", "codex", "hermes", "opencode")

# Bundled agents that intentionally do *not* go through agent_usage.py.
NO_SHARED_USAGE_IDS = ("claude", "deepseek", "antigravity")


def _meters_payload(md: str) -> Dict[str, Any]:
    assert "<cuttle_meters>" in md, f"missing cuttle_meters in:\n{md[:400]}"
    raw = md.split("<cuttle_meters>", 1)[1].split("</cuttle_meters>", 1)[0].strip()
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    assert payload.get("variant") == "usage"
    rows = payload.get("rows")
    assert isinstance(rows, list) and rows, "usage meters must include at least one row"
    for row in rows:
        assert isinstance(row, dict)
        assert str(row.get("label") or "").strip()
        if row.get("disabled"):
            assert "status" in row or "pct" in row
        else:
            assert "pct" in row
            assert isinstance(row["pct"], (int, float))
    return payload


def _assert_usage_reply(md: str, *, title_substr: str) -> Dict[str, Any]:
    assert isinstance(md, str) and md.strip()
    assert not md.startswith("❌"), f"usage reply should succeed, got error:\n{md[:400]}"
    assert title_substr.lower() in md.lower()
    return _meters_payload(md)


# ---------------------------------------------------------------------------
# Shared helpers / parsers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("/usage", ""),
        ("/usage 7d", "7d"),
        ("usage", ""),
        ("usage 14", "14"),
        ("/USAGE now", "now"),
        ("/model", None),
        ("hello", None),
        ("", None),
    ],
)
def test_parse_usage_slash_matrix(prompt, expected):
    assert parse_usage_slash(prompt) == expected


def test_cuttle_meters_markdown_contract():
    md = cuttle_meters_markdown(
        [
            {"label": "Included", "pct": 3.36},
            {"label": "On-Demand", "pct": 0, "disabled": True, "status": "Off"},
        ]
    )
    payload = _meters_payload(md)
    assert payload["rows"][0]["pct"] == 3.36
    assert payload["rows"][1]["disabled"] is True
    assert payload["rows"][1]["status"] == "Off"


def test_cuttle_meters_renderer_wired_in_frontend():
    js = CHAT_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert "cuttle_meters" in js
    assert "CUTTLE_METERS_" in js
    assert "cuttle-meters" in css
    assert "cuttle-meter-fill" in css


# ---------------------------------------------------------------------------
# Formatter fixtures (offline, no network)
# ---------------------------------------------------------------------------


CURSOR_USAGE_FAKE = {
    "success": True,
    "period": {
        "billingCycleStart": "1787335890000",
        "billingCycleEnd": "1790014290000",
        "displayMessage": "You've used 39% of your included usage",
        "autoModelSelectedDisplayMessage": "You've used 2% of your included total usage",
        "namedModelSelectedDisplayMessage": "You've used 17% of your included API usage",
    },
    "plan_info": {"planName": "Pro", "price": "$20/mo", "includedAmountCents": 2000},
    "plan_usage": {
        "totalSpend": 772,
        "includedSpend": 772,
        "remaining": 1228,
        "limit": 2000,
        "totalPercentUsed": 3.0,
        "autoPercentUsed": 3.0,
        "apiPercentUsed": 0.0,
    },
    "hard_limit": {"noUsageBasedAllowed": True},
    "stripe": {"membershipType": "pro", "subscriptionStatus": "active"},
    "aggregations": [],
}

CODEX_USAGE_FAKE = {
    "success": True,
    "plan_type": "plus",
    "email": "dev@example.com",
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 42.5,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 3600,
        },
        "secondary_window": {
            "used_percent": 10.0,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 86400,
        },
    },
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
    "rate_limit_reset_credits": {"available_count": 2},
}

MUSE_USAGE_FAKE = {
    "success": True,
    "email": "dev@example.com",
    "name": "Dev",
    "mechanism": "oauth",
    "obtained_via": "device_code",
    "days": 30,
    "local": {
        "sessions": 2,
        "prompt": 1000,
        "completion": 200,
        "context": 5000,
        "by_model": {
            "muse-spark-1.3": {
                "prompt": 800,
                "completion": 150,
                "context": 4000,
                "sessions": 1,
            },
            "muse-spark-1.3-contributor": {
                "prompt": 200,
                "completion": 50,
                "context": 1000,
                "sessions": 1,
            },
        },
    },
}

HERMES_INSIGHTS_SAMPLE = """
  Period: Sep 17, 2026 — Sep 18, 2026

  Overview
  Sessions:          20            Messages:        1,057
  Tool calls:        565
  Input tokens:      2,860,252     Output tokens:   166,377
  Total tokens:      26,061,509

  Models Used
  ────────────────────────────────────────────────────────
  Model                          Sessions       Tokens
  glm-5.3-flash                        13   21,497,091
  deepseek-v4.1-flash                   7    4,564,418

  Top Tools
  ────────────────────────────────────────────────────────
  Tool                            Calls        %
  read_file                         206    36.5%
  terminal                          187    33.1%
"""

OPENCODE_STATS_SAMPLE = """
┌────────────────────────────────────────────────────────┐
│                       OVERVIEW                         │
├────────────────────────────────────────────────────────┤
│Sessions                                              6 │
│Messages                                            339 │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│                    COST & TOKENS                       │
├────────────────────────────────────────────────────────┤
│Total Cost                                        $1.07 │
│Avg Cost/Day                                      $0.04 │
│Input                                              2.9M │
│Output                                           130.9K │
│Cache Read                                        52.9M │
└────────────────────────────────────────────────────────┘

│ openrouter/z-ai/glm-5.3-flash                          │
│  Cost                                          $1.0683 │

│ edit               ████████████████████ 141 (34.9%)    │
│ bash               ███████████████      107 (26.5%)    │
"""


@pytest.mark.parametrize(
    "formatter,payload,title",
    [
        (format_cursor_usage_markdown, CURSOR_USAGE_FAKE, "Cursor"),
        (format_codex_usage_markdown, CODEX_USAGE_FAKE, "Codex"),
        (format_muse_usage_markdown, MUSE_USAGE_FAKE, "Muse"),
    ],
)
def test_formatters_emit_cuttle_meters(formatter, payload, title):
    md = formatter(payload)
    _assert_usage_reply(md, title_substr=title)


def test_hermes_parse_and_format_meters():
    parsed = parse_hermes_insights_text(HERMES_INSIGHTS_SAMPLE)
    assert parsed["sessions"] == 20
    assert parsed["tools"][0]["name"] == "read_file"
    md = format_hermes_usage_markdown({**parsed, "success": True, "days": 30})
    payload = _assert_usage_reply(md, title_substr="Hermes")
    labels = [r["label"] for r in payload["rows"]]
    assert "read_file" in labels or "glm-5.3-flash" in labels


def test_opencode_parse_and_format_meters():
    parsed = parse_opencode_stats_text(OPENCODE_STATS_SAMPLE)
    assert parsed["sessions"] == 6
    assert parsed["total_cost"] == pytest.approx(1.07)
    assert parsed["models"][0]["id"].startswith("openrouter/")
    md = format_opencode_usage_markdown({**parsed, "success": True, "days": 30})
    payload = _assert_usage_reply(md, title_substr="OpenCode")
    labels = [r["label"] for r in payload["rows"]]
    assert "edit" in labels or any("glm" in x for x in labels)


@pytest.mark.parametrize(
    "formatter",
    [
        format_cursor_usage_markdown,
        format_codex_usage_markdown,
        format_muse_usage_markdown,
        format_hermes_usage_markdown,
        format_opencode_usage_markdown,
    ],
)
def test_formatters_error_path(formatter):
    md = formatter({"success": False, "error": "boom"})
    assert md.startswith("❌")
    assert "boom" in md
    assert "<cuttle_meters>" not in md


# ---------------------------------------------------------------------------
# Router: handle_agent_usage_slash
# ---------------------------------------------------------------------------


def test_handle_agent_usage_slash_routes_all_non_cursor(monkeypatch):
    monkeypatch.setattr(
        "api.agent_usage.run_codex_usage",
        lambda: format_codex_usage_markdown(CODEX_USAGE_FAKE),
    )
    monkeypatch.setattr(
        "api.agent_usage.run_muse_usage",
        lambda days=30: format_muse_usage_markdown({**MUSE_USAGE_FAKE, "days": days}),
    )
    monkeypatch.setattr(
        "api.agent_usage.run_hermes_usage",
        lambda days=30: format_hermes_usage_markdown(
            {**parse_hermes_insights_text(HERMES_INSIGHTS_SAMPLE), "success": True, "days": days}
        ),
    )
    monkeypatch.setattr(
        "api.agent_usage.run_opencode_usage",
        lambda days=30: format_opencode_usage_markdown(
            {**parse_opencode_stats_text(OPENCODE_STATS_SAMPLE), "success": True, "days": days}
        ),
    )

    for agent_id, title in (
        ("codex", "Codex"),
        ("muse", "Muse"),
        ("hermes", "Hermes"),
        ("opencode", "OpenCode"),
    ):
        for prompt in ("/usage", "usage", "/usage 7d"):
            md = handle_agent_usage_slash(agent_id, prompt)
            assert md is not None, f"{agent_id} {prompt!r} should handle usage"
            _assert_usage_reply(md, title_substr=title)

    assert handle_agent_usage_slash("codex", "/model") is None
    assert handle_agent_usage_slash("cursor", "/usage") is None  # Cursor has its own path
    for aid in NO_SHARED_USAGE_IDS:
        assert handle_agent_usage_slash(aid, "/usage") is None


# ---------------------------------------------------------------------------
# Adapter handle_meta — each usage harness answers without a CLI turn
# ---------------------------------------------------------------------------


def test_cursor_handle_meta_usage(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    cwd = str(tmp_path / "proj")
    (tmp_path / "proj").mkdir()

    with patch(
        "api.cursor_agent_commands.fetch_cursor_account_usage",
        return_value=CURSOR_USAGE_FAKE,
    ):
        # Direct slash handler (same path Cursor adapter uses).
        out = handle_cursor_agent_slash("/usage", cwd=cwd, chat_session_id="s1")
        assert out and out["action"] == "reply"
        _assert_usage_reply(out["message"], title_substr="Cursor")

        from api.agent_harness.agents.cursor.adapter import Adapter

        result = Adapter().handle_meta(
            "/usage", chat_session_id="s1", model="auto", cwd=cwd
        )
        assert result is not None
        assert result.success
        _assert_usage_reply(result.output, title_substr="Cursor")


def test_muse_handle_meta_usage(monkeypatch):
    monkeypatch.setattr(
        "api.agent_usage.run_muse_usage",
        lambda days=30: format_muse_usage_markdown({**MUSE_USAGE_FAKE, "days": days}),
    )
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.load_muse_model",
        lambda _sid: None,
    )
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_tool.resolve_muse_default_model",
        lambda: "muse-spark-1.3",
    )
    from api.agent_harness.agents.muse.adapter import Adapter

    result = Adapter().handle_meta("/usage", chat_session_id="42", model=None)
    assert result is not None and result.success
    _assert_usage_reply(result.output, title_substr="Muse")
    assert Adapter().handle_meta("write a test", chat_session_id="42", model=None) is None


def test_codex_handle_meta_usage(monkeypatch):
    monkeypatch.setattr(
        "api.agent_usage.run_codex_usage",
        lambda: format_codex_usage_markdown(CODEX_USAGE_FAKE),
    )
    monkeypatch.setattr(
        "scripts.utilities.codex_cli_session_store.load_codex_model",
        lambda _sid: None,
    )
    from api.agent_harness.agents.codex.adapter import Adapter

    result = Adapter().handle_meta("/usage", chat_session_id="42", model=None)
    assert result is not None and result.success
    payload = _assert_usage_reply(result.output, title_substr="Codex")
    labels = " ".join(r["label"] for r in payload["rows"]).lower()
    assert "5-hour" in labels or "weekly" in labels


def test_hermes_handle_meta_usage(monkeypatch):
    monkeypatch.setattr(
        "api.agent_usage.run_hermes_usage",
        lambda days=30: format_hermes_usage_markdown(
            {**parse_hermes_insights_text(HERMES_INSIGHTS_SAMPLE), "success": True, "days": days}
        ),
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_session_store.load_hermes_model",
        lambda _sid: None,
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_tool.resolve_hermes_default_model",
        lambda: "glm-5.3-flash",
    )
    from api.agent_harness.agents.hermes.adapter import Adapter

    result = Adapter().handle_meta("usage 7d", chat_session_id="42", model=None)
    assert result is not None and result.success
    _assert_usage_reply(result.output, title_substr="Hermes")


def test_opencode_handle_meta_usage(monkeypatch):
    monkeypatch.setattr(
        "api.agent_usage.run_opencode_usage",
        lambda days=30: format_opencode_usage_markdown(
            {**parse_opencode_stats_text(OPENCODE_STATS_SAMPLE), "success": True, "days": days}
        ),
    )
    monkeypatch.setattr(
        "api.agent_harness.agents.opencode.session_store.load_opencode_model",
        lambda _sid: None,
    )
    monkeypatch.setattr(
        "api.agent_harness.agent_defaults.get_starred_model",
        lambda _aid: None,
    )
    from api.agent_harness.agents.opencode.adapter import Adapter

    result = Adapter().handle_meta("/usage", chat_session_id="42", model=None)
    assert result is not None and result.success
    _assert_usage_reply(result.output, title_substr="OpenCode")


@pytest.mark.parametrize("agent_id", USAGE_HARNESS_IDS)
def test_every_usage_harness_adapter_exposes_handle_meta(agent_id):
    from api.agent_harness.catalog import get_agent, reload_catalog

    reload_catalog()
    pair = get_agent(agent_id)
    assert pair is not None, f"missing harness agent {agent_id}"
    _manifest, adapter = pair
    assert callable(getattr(adapter, "handle_meta", None)), (
        f"{agent_id} adapter must implement handle_meta for /usage"
    )


# ---------------------------------------------------------------------------
# Palette / frontend gating
# ---------------------------------------------------------------------------


def test_palette_usage_gated_per_harness_not_global():
    js = CHAT_JS.read_text(encoding="utf-8")
    # Registry tables moved to chat_slash.js (Phase 3 Slice 2); gating
    # assembly stays in chat_page.js.
    slash_js = SLASH_JS.read_text(encoding="utf-8")

    # Cursor nested list.
    c_start = slash_js.find("const CURSOR_AGENT_SLASH_COMMANDS = [")
    c_end = slash_js.find("];", c_start)
    cursor_block = slash_js[c_start:c_end]
    assert "prefix: '/usage'" in cursor_block
    assert "category: 'cursor-cmd'" in cursor_block

    # Other harnesses share HARNESS_USAGE_SLASH_BY_AGENT.
    assert "HARNESS_USAGE_SLASH_BY_AGENT" in slash_js
    assert "function harnessUsageSlashCommandsForPalette(" in js
    for agent, cat in (
        ("muse", "muse-cmd"),
        ("codex", "codex-cmd"),
        ("hermes", "hermes-cmd"),
        ("opencode", "opencode-cmd"),
    ):
        assert f"category: '{cat}'" in slash_js
        assert agent in slash_js  # agent key present in HARNESS_USAGE_SLASH_BY_AGENT

    # Explicit chip gates used by harnessUsageSlashCommandsForPalette.
    for fn in (
        "hasActiveMuseAgentChip",
        "hasActiveCodexAgentChip",
        "hasActiveHermesAgentChip",
        "hasActiveOpenCodeAgentChip",
        "hasActiveCursorAgentChip",
    ):
        assert f"function {fn}(" in js

    # Must not appear in the global sticky/agent list.
    s_start = slash_js.find("const SLASH_COMMANDS = [")
    s_end = slash_js.find("const CURSOR_AGENT_SLASH_COMMANDS", s_start)
    base = js[s_start:s_end]
    assert "prefix: '/usage'" not in base

    # Palette merge includes both cursor nested cmds and harness usage cmds.
    filt = js[js.find("function filterSlashPaletteItems(") : js.find("function filterSlashPaletteItems(") + 4500]
    assert "cursorAgentSlashCommandsForPalette()" in filt
    assert "harnessUsageSlashCommandsForPalette()" in filt


def test_palette_chip_categories_map_usage_to_agent():
    js = CHAT_JS.read_text(encoding="utf-8")
    slash_js = SLASH_JS.read_text(encoding="utf-8")
    # composerChipAgentId must map *-cmd categories to the right agent.
    assert "'muse-cmd': 'muse'" in slash_js.replace(" ", "") or "'muse-cmd': 'muse'" in slash_js
    body = slash_js[slash_js.find("function composerChipAgentId(") : slash_js.find("function composerChipAgentId(") + 1800]
    for pair in (
        ("muse-cmd", "muse"),
        ("codex-cmd", "codex"),
        ("hermes-cmd", "hermes"),
        ("opencode-cmd", "opencode"),
        ("cursor-cmd", "cursor"),
    ):
        assert pair[0] in body and pair[1] in body


def test_is_sendable_treats_nested_usage_as_real_turn():
    # Phase 3 Slice 6: sendability lives in chat_composer.js (page keeps a
    # thin adapter); pin the nested one-shot patterns at their owned module.
    composer_js = (WEB / "js" / "chat_composer.js").read_text(encoding="utf-8")
    assert "usage" in composer_js
    assert re.search(r"\/\(usage\|", composer_js) or "/usage" in composer_js
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "function isSendableComposerMessage(message, attachments)" in js


# ---------------------------------------------------------------------------
# Cross-harness contract: every supported agent produces meters when mocked
# ---------------------------------------------------------------------------


def test_all_usage_harnesses_produce_meters_via_adapters(monkeypatch, tmp_path):
    """One matrix test: every usage-capable harness answers ``/usage`` with meters."""
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    (tmp_path / "proj").mkdir()
    cwd = str(tmp_path / "proj")

    monkeypatch.setattr(
        "api.cursor_agent_commands.fetch_cursor_account_usage",
        lambda: CURSOR_USAGE_FAKE,
    )
    monkeypatch.setattr(
        "api.agent_usage.run_muse_usage",
        lambda days=30: format_muse_usage_markdown({**MUSE_USAGE_FAKE, "days": days}),
    )
    monkeypatch.setattr(
        "api.agent_usage.run_codex_usage",
        lambda: format_codex_usage_markdown(CODEX_USAGE_FAKE),
    )
    monkeypatch.setattr(
        "api.agent_usage.run_hermes_usage",
        lambda days=30: format_hermes_usage_markdown(
            {**parse_hermes_insights_text(HERMES_INSIGHTS_SAMPLE), "success": True, "days": days}
        ),
    )
    monkeypatch.setattr(
        "api.agent_usage.run_opencode_usage",
        lambda days=30: format_opencode_usage_markdown(
            {**parse_opencode_stats_text(OPENCODE_STATS_SAMPLE), "success": True, "days": days}
        ),
    )

    # Session/model loaders used by handle_meta — keep them no-ops.
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.load_muse_model", lambda _s: None
    )
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_tool.resolve_muse_default_model",
        lambda: "muse-spark-1.3",
    )
    monkeypatch.setattr(
        "scripts.utilities.codex_cli_session_store.load_codex_model", lambda _s: None
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_session_store.load_hermes_model", lambda _s: None
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_tool.resolve_hermes_default_model",
        lambda: "glm-5.3-flash",
    )
    monkeypatch.setattr(
        "api.agent_harness.agents.opencode.session_store.load_opencode_model",
        lambda _s: None,
    )
    monkeypatch.setattr(
        "api.agent_harness.agent_defaults.get_starred_model", lambda _a: None
    )

    from api.agent_harness.agents.cursor.adapter import Adapter as CursorAdapter
    from api.agent_harness.agents.muse.adapter import Adapter as MuseAdapter
    from api.agent_harness.agents.codex.adapter import Adapter as CodexAdapter
    from api.agent_harness.agents.hermes.adapter import Adapter as HermesAdapter
    from api.agent_harness.agents.opencode.adapter import Adapter as OpenCodeAdapter

    cases: List[tuple] = [
        ("cursor", CursorAdapter(), "Cursor", {"cwd": cwd}),
        ("muse", MuseAdapter(), "Muse", {}),
        ("codex", CodexAdapter(), "Codex", {}),
        ("hermes", HermesAdapter(), "Hermes", {}),
        ("opencode", OpenCodeAdapter(), "OpenCode", {}),
    ]

    seen = []
    for agent_id, adapter, title, extra in cases:
        kwargs = {"chat_session_id": "sess", "model": None, **extra}
        result = adapter.handle_meta("/usage", **kwargs)
        assert result is not None, f"{agent_id} handle_meta(/usage) returned None"
        assert result.success, f"{agent_id} usage failed: {result.error or result.output}"
        _assert_usage_reply(result.output, title_substr=title)
        seen.append(agent_id)

    assert tuple(seen) == USAGE_HARNESS_IDS
