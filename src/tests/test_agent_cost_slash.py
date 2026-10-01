"""``/cost`` — per-model token pricing for every Cuttle agent harness.

Coverage matrix
---------------
* Parser (``/cost``, ``/pricing``, bare ``cost`` only as a whole prompt)
* models.dev lookup: provider preference, harness suffix stripping
  (``cursor-grok-4.6-high-fast`` → ``grok-4.6``), provider key beats a
  reseller's slash-bearing id, ``-preview`` fallback
* Manifest pricing fields (source / providers / overrides) for every bundled harness
* OpenCode first-class rates from ``opencode models --verbose``
* Every harness (cursor, codex, muse, claude, deepseek, antigravity, hermes,
  opencode) answers ``/cost`` with a ``<cuttle_pricing>`` table, active model first
* Kernel intercept: no CLI turn, works with the CLI missing
* Frontend: renderer highlights the active row; palette offers ``/cost`` per badge

No network / CLI calls — catalogs, pins and the models.dev cache are faked.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from api import model_pricing as mp
from api.agent_cost import (
    build_cost_table,
    cuttle_pricing_markdown,
    fmt_rate,
    handle_agent_cost_slash,
    parse_cost_slash,
    price_model,
    run_agent_cost,
)
from api.agent_harness.types import AgentManifest

HARNESS_IDS = ("cursor", "codex", "muse", "claude", "deepseek", "antigravity", "hermes", "opencode")

WEB = Path(__file__).resolve().parents[1] / "web"
CHAT_JS = WEB / "js" / "chat_page.js"
SLASH_JS = WEB / "js" / "chat_slash.js"
CHAT_CSS = WEB / "css" / "chat_page.css"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PRICING_ENTRIES: List[Dict[str, Any]] = [
    {"provider": "openai", "model_id": "gpt-6-luna", "name": "GPT-6 Luna", "input": 0.1, "output": 0.5, "cache_read": 0.01, "context": 400000},
    {"provider": "openai", "model_id": "gpt-6-sol", "name": "GPT-6 Sol", "input": 2.0, "output": 10.0, "cache_read": 0.2},
    {"provider": "openai", "model_id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "input": 4.0, "output": 20.0},
    {"provider": "openrouter", "model_id": "openai/gpt-6-luna", "name": "GPT-6 Luna (OR)", "input": 0.3, "output": 0.9},
    {"provider": "xai", "model_id": "grok-4.6", "name": "Grok 4.6", "input": 2.0, "output": 6.0, "cache_read": 0.5},
    {"provider": "anthropic", "model_id": "claude-opus-5", "name": "Claude Opus 5", "input": 5.0, "output": 25.0},
    {"provider": "anthropic", "model_id": "claude-opus-5-5", "name": "Claude Opus 5.5", "input": 4.0, "output": 20.0},
    {"provider": "anthropic", "model_id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "input": 3.0, "output": 15.0},
    {"provider": "anthropic", "model_id": "claude-sonnet-5", "name": "Claude Sonnet 5", "input": 2.0, "output": 10.0},
    {"provider": "anthropic", "model_id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5 (dated)", "input": 3.0, "output": 15.0},
    {"provider": "anthropic", "model_id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "input": 1.0, "output": 5.0},
    {"provider": "meta", "model_id": "muse-spark-1.3", "name": "Muse Spark 1.3", "input": 1.25, "output": 4.25},
    {"provider": "meta", "model_id": "muse-spark-1.3-contributor", "name": "Muse Spark 1.3 Contributor", "input": 0.1, "output": 0.2},
    # Reseller listed first with a slash id identical to the DeepSeek provider key.
    {"provider": "tokengo", "model_id": "deepseek/deepseek-v4-flash", "name": "DS Flash (reseller)", "input": 0.098, "output": 0.196},
    {"provider": "deepseek", "model_id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "input": 0.15, "output": 0.6},
    {"provider": "deepseek", "model_id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "input": 0.435, "output": 0.87},
    {"provider": "google", "model_id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash", "input": 0.75, "output": 3.75},
    {"provider": "google", "model_id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview", "input": 2.0, "output": 12.0},
    {"provider": "poe", "model_id": "gemini-3.1-pro", "name": "Gemini 3.1 Pro (poe)", "input": 1.5, "output": 9.0},
    {"provider": "openrouter", "model_id": "z-ai/glm-5.3-flash", "name": "GLM 5.3 Flash", "input": 0.15, "output": 0.5},
    {"provider": "openrouter", "model_id": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "input": 0.14, "output": 0.42},
]


@pytest.fixture()
def pricing_cache(tmp_path, monkeypatch):
    cache = tmp_path / "models_dev_pricing_cache.json"
    monkeypatch.setattr(mp, "_cache_path", lambda: cache)
    monkeypatch.setattr(mp, "_index", {})
    monkeypatch.setattr(mp, "_context_index", {})
    monkeypatch.setattr(mp, "_schedule_background_refresh", lambda **_k: None)
    cache.write_text(
        json.dumps(
            {
                "version": mp._CACHE_VERSION,
                "fetched_at": 9_999_999_999.0,  # never stale → no refresh attempts
                "source": "test",
                "count": len(PRICING_ENTRIES),
                "entries": PRICING_ENTRIES,
            }
        ),
        encoding="utf-8",
    )
    return cache


@pytest.fixture(autouse=True)
def no_benchmarks(monkeypatch):
    """Never download Epoch AI / SWE-bench; tests opt into ``bench_data``."""
    monkeypatch.setattr(
        "api.task_benchmarks.load_entries", lambda **_k: {"entries": [], "fetched_at": 0}
    )


def _bench(bench, model, effort, cost, score, date="2026-09-01"):
    from api.task_benchmarks import model_key

    return {"bench": bench, "model": model, "key": model_key(model), "effort": effort,
            "cost": cost, "score": score, "tokens": None, "date": date}


BENCH_ENTRIES = [
    _bench("cursorbench", "grok-4.6", "low", 1.2, 30.0),
    _bench("cursorbench", "grok-4.6", "medium", 3.48, 36.1),
    _bench("cursorbench", "grok-4.6", "high", 5.2, 40.4),
    _bench("cursorbench", "claude-opus-5", "medium", 6.94, 43.3),
    _bench("cursorbench", "claude-opus-5", "low", 4.87, 40.7),
    _bench("cursorbench", "Composer 2.5", "", 0.68, 27.7),
    _bench("cursorbench", "gpt-5.6-sol", "medium", 1.77, 31.1),
    _bench("cursorbench", "gpt-6-sol", "medium", 2.5, 35.0),
    _bench("deepswe", "gpt-6-luna", "low", 0.4, 50.0),
    _bench("deepswe", "gpt-6-luna", "medium", 0.9, 55.0),
    _bench("swebench", "gpt-6-sol", "high", 0.52, 71.8),
]


@pytest.fixture()
def bench_data(monkeypatch):
    monkeypatch.setattr(
        "api.task_benchmarks.load_entries",
        lambda **_k: {"entries": [dict(e) for e in BENCH_ENTRIES], "fetched_at": 9_999_999_999.0},
    )


@pytest.fixture()
def no_pins(monkeypatch):
    """No chat pins, no starred defaults — tests opt in per harness."""
    monkeypatch.setattr("api.agent_harness.agent_defaults.get_starred_model", lambda _a: None)
    monkeypatch.setattr("api.agent_harness.agent_defaults.get_starred_effort", lambda _a: None)
    monkeypatch.setattr("api.agent_cost._session_pin", lambda _aid, _sid, _cwd: ("", ""))


CURSOR_MODELS = [
    {"id": "auto", "label": "Auto", "current": True},
    {"id": "composer-2.5", "label": "Composer 2.5"},
    {"id": "cursor-grok-4.6-low", "label": "Grok 4.6 Low"},
    {"id": "cursor-grok-4.6-high", "label": "Grok 4.6"},
    {"id": "cursor-grok-4.6-high-fast", "label": "Grok 4.6 Fast"},
    {"id": "claude-opus-5-thinking-high", "label": "Claude Opus 5 1M Thinking"},
    {"id": "claude-opus-5-low", "label": "Claude Opus 5 1M Low"},
    {"id": "gpt-5.6-sol-high", "label": "GPT-5.6 Sol 1M High"},
]

CODEX_MODELS = [
    {"id": "gpt-6-sol", "label": "GPT-6 Sol", "efforts": ["low", "high"]},
    {"id": "gpt-6-luna", "label": "GPT-6 Luna", "efforts": ["low", "high"]},
    {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol", "efforts": ["low"]},
    {"id": "codex-auto-review", "label": "Codex Auto Review", "efforts": []},
]

OPENCODE_VERBOSE = """
openrouter/z-ai/glm-5.3-flash
{
  "id": "z-ai/glm-5.3-flash",
  "providerID": "openrouter",
  "name": "GLM-5.3-Flash",
  "cost": {"input": 0.15, "output": 0.5, "cache": {"read": 0.03, "write": 0}},
  "limit": {"context": 202752, "output": 32000},
  "variants": {"low": {}, "high": {}}
}
opencode/big-pickle
{
  "id": "big-pickle",
  "providerID": "opencode",
  "name": "Big Pickle",
  "cost": {"input": 0, "output": 0, "cache": {"read": 0, "write": 0}},
  "limit": {"context": 200000}
}
"""


def _fake_catalogs(monkeypatch):
    monkeypatch.setattr("api.cursor_agent_commands.list_cursor_agent_models", lambda: [dict(m) for m in CURSOR_MODELS])
    monkeypatch.setattr(
        "api.agent_harness.agents.codex.model_catalog.list_codex_catalog_models",
        lambda refresh=False: {"models": [dict(m) for m in CODEX_MODELS], "source": "cli_bundled"},
    )
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_tool.list_muse_catalog_models",
        lambda refresh=False: {
            "models": [
                {"id": "muse-spark-1.3", "label": "Muse Spark 1.3"},
                {"id": "muse-spark-1.3-contributor", "label": "Muse Spark 1.3 (Contributor)"},
            ]
        },
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_tool.list_hermes_known_models",
        lambda: [
            {"id": "z-ai/glm-5.3-flash", "label": "GLM 5.3 Flash", "provider": "openrouter"},
            {"id": "qwen3-coder", "label": "Qwen3 Coder (local)", "provider": "custom"},
            {"id": "deepseek/deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash", "provider": "openrouter"},
        ],
    )
    from api.agent_harness.agents.opencode import model_catalog as oc

    rows = oc._parse_models_stdout(OPENCODE_VERBOSE)
    monkeypatch.setattr(
        "api.agent_harness.agents.opencode.model_catalog.list_opencode_catalog_models",
        lambda refresh=False, **_k: {"models": [dict(r) for r in rows], "source": "cache"},
    )
    # CLI defaults that would otherwise read local config files.
    monkeypatch.setattr("scripts.utilities.muse_cli_tool.resolve_muse_default_model", lambda: "muse-spark-1.3")
    monkeypatch.setattr("scripts.utilities.hermes_cli_tool.resolve_hermes_default_model", lambda: "z-ai/glm-5.3-flash")
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_tool.resolve_hermes_runtime",
        lambda model=None, provider=None: {"model": model or "", "provider": "openrouter"},
    )


def _manifest(agent_id: str) -> AgentManifest:
    from api.agent_harness.catalog import get_agent, reload_catalog

    reload_catalog()
    pair = get_agent(agent_id)
    assert pair is not None, f"missing bundled harness {agent_id}"
    return pair[0]


def _pricing_payload(md: str) -> Dict[str, Any]:
    assert "<cuttle_pricing>" in md, f"missing cuttle_pricing in:\n{md[:600]}"
    raw = md.split("<cuttle_pricing>", 1)[1].split("</cuttle_pricing>", 1)[0].strip()
    payload = json.loads(raw)
    assert isinstance(payload.get("rows"), list) and payload["rows"]
    for row in payload["rows"]:
        assert str(row.get("model") or "").strip()
    return payload


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("/cost", ""),
        ("/cost sol", "sol"),
        ("/COST refresh", "refresh"),
        ("/pricing", ""),
        ("/prices all", "all"),
        ("cost", ""),
        ("pricing", ""),
        # Bare words followed by a task are real prompts, not the command.
        ("cost of this refactor?", None),
        ("costs are too high, optimize the loop", None),
        ("/costly", None),
        ("/usage", None),
        ("", None),
    ],
)
def test_parse_cost_slash_matrix(prompt, expected):
    assert parse_cost_slash(prompt) == expected


def test_fmt_rate():
    assert fmt_rate(None) == "—"
    assert fmt_rate(0) == "Free"
    assert fmt_rate(0.1) == "$0.10"
    assert fmt_rate(2) == "$2.00"
    assert fmt_rate(0.435) == "$0.435"
    assert fmt_rate(0.0036) == "$0.0036"


# ---------------------------------------------------------------------------
# models.dev lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,base",
    [
        ("cursor-grok-4.6-high-fast", "grok-4.6"),
        ("claude-opus-5-thinking-high", "claude-opus-5"),
        ("claude-opus-5-5-max", "claude-opus-5-5"),
        ("gpt-5.6-sol-xhigh", "gpt-5.6-sol"),
        ("gemini-3.7-flash-medium", "gemini-3.7-flash"),
        ("gpt-6-luna", "gpt-6-luna"),
        ("muse-spark-1.3-contributor", "muse-spark-1.3-contributor"),
        ("openrouter/z-ai/glm-5.3-flash", "glm-5.3-flash"),
    ],
)
def test_strip_variant_suffixes(model, base):
    assert mp.strip_variant_suffixes(model) == base


def test_lookup_candidates_most_specific_first():
    cands = mp.pricing_lookup_candidates("cursor-grok-4.6-high-fast")
    assert cands[0] == "cursor-grok-4.6-high-fast"
    assert "grok-4.6" in cands
    # Exact id is tried before any stripping (real ids may end in -max).
    assert mp.pricing_lookup_candidates("gpt-5.1-codex-max")[0] == "gpt-5.1-codex-max"


def test_lookup_prefers_manifest_provider(pricing_cache):
    hit = mp.lookup_model_pricing("gpt-6-luna", providers=["openai"])
    assert hit and hit["provider"] == "openai" and hit["input"] == 0.1
    assert hit["name"] == "GPT-6 Luna"
    assert hit["context"] == 400000


def test_lookup_strips_harness_suffixes(pricing_cache):
    hit = mp.lookup_model_pricing("cursor-grok-4.6-high-fast", providers=["xai"])
    assert hit and hit["model_id"] == "grok-4.6"
    assert mp.lookup_model_pricing("claude-opus-5-thinking-high", providers=["anthropic"])["input"] == 5.0


def test_provider_key_beats_reseller_slash_id(pricing_cache):
    hit = mp.lookup_model_pricing("deepseek-v4-flash", providers=["deepseek"])
    assert hit["provider"] == "deepseek"
    assert hit["input"] == 0.15
    # The generic lookup must also resolve the provider-qualified key to DeepSeek.
    assert mp.lookup_model_rates("deepseek/deepseek-v4-flash")["provider"] == "deepseek"


def test_lookup_preview_fallback_for_vendor(pricing_cache):
    hit = mp.lookup_model_pricing("gemini-3.1-pro-high", providers=["google"])
    assert hit["provider"] == "google"
    assert hit["model_id"] == "gemini-3.1-pro-preview"


def test_lookup_skips_auto(pricing_cache):
    assert mp.lookup_model_pricing("auto", providers=["openai"]) is None


def test_list_provider_pricing(pricing_cache):
    ids = {r["model_id"] for r in mp.list_provider_pricing("anthropic")}
    assert {"claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5"} <= ids
    assert "gpt-6-luna" not in ids


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", HARNESS_IDS)
def test_every_bundled_harness_declares_pricing(agent_id):
    m = _manifest(agent_id)
    assert m.pricing_source in ("cli", "models_dev")
    if m.pricing_source == "models_dev":
        assert m.pricing_providers, f"{agent_id} must name its models.dev providers"


def test_opencode_is_first_class_cli_pricing():
    assert _manifest("opencode").pricing_source == "cli"


def test_manifest_pricing_fields_parse():
    from api.agent_harness.catalog import _manifest_from_dict

    m = _manifest_from_dict(
        {
            "id": "dropin",
            "pricing_source": "models_dev",
            "pricing_providers": "OpenAI",
            "pricing": {"house-model": {"input": 1.5, "output": 6, "note": "vendor quote"}},
        },
        fallback_id="dropin",
        source="user",
    )
    assert m.pricing_providers == ["openai"]
    assert m.pricing["house-model"]["input"] == 1.5


# ---------------------------------------------------------------------------
# Rate sources
# ---------------------------------------------------------------------------


def test_opencode_verbose_keeps_cost_block():
    from api.agent_harness.agents.opencode.model_catalog import _parse_models_stdout

    rows = {r["id"]: r for r in _parse_models_stdout(OPENCODE_VERBOSE)}
    glm = rows["openrouter/z-ai/glm-5.3-flash"]
    assert glm["cost"] == {"input": 0.15, "output": 0.5, "cache_read": 0.03, "cache_write": 0.0}
    assert glm["context"] == 202752
    assert rows["opencode/big-pickle"]["cost"]["input"] == 0.0


def test_price_model_sources(pricing_cache):
    opencode = AgentManifest(id="opencode", label="OpenCode", slash="/opencode", pricing_source="cli")
    cli = price_model(opencode, "x/y", catalog_row={"cost": {"input": 1.0, "output": 2.0}, "context": 1000})
    assert cli["source"] == "cli" and cli["input"] == 1.0 and cli["context"] == 1000

    dropin = AgentManifest(
        id="dropin",
        label="Drop-in",
        slash="/dropin",
        pricing={"house-model": {"input": 1.5, "output": 6, "note": "vendor quote"}},
    )
    over = price_model(dropin, "house-model-high")
    assert over["source"] == "manifest" and over["output"] == 6.0 and over["note"] == "vendor quote"

    hermes = AgentManifest(id="hermes", label="Hermes", slash="/hermes", pricing_providers=["openrouter"])
    local = price_model(hermes, "qwen3-coder", provider_hint="custom")
    assert local["source"] == "local" and local["input"] == 0.0

    claude = AgentManifest(id="claude", label="Claude", slash="/claude", pricing_providers=["anthropic"])
    alias = price_model(claude, "sonnet")
    assert alias["resolved"] == "claude-sonnet-5"  # newest, dated snapshot skipped
    assert alias["input"] == 2.0
    assert price_model(claude, "opus")["resolved"] == "claude-opus-5-5"


def test_zero_rates_survive_the_json_block():
    md = cuttle_pricing_markdown([{"model": "local", "label": "Local", "input": 0.0, "output": 0.0, "active": False}])
    row = _pricing_payload(md)["rows"][0]
    assert row["input"] == 0.0 and row["output"] == 0.0
    assert "active" not in row


# ---------------------------------------------------------------------------
# Every harness answers /cost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", HARNESS_IDS)
def test_every_harness_answers_cost(agent_id, pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    m = _manifest(agent_id)
    md = handle_agent_cost_slash(m, "/cost", chat_session_id="42", cwd="")
    assert md is not None and not md.startswith("❌"), md[:400]
    assert f"{m.label} — model pricing" in md
    payload = _pricing_payload(md)
    assert any(r.get("input") is not None for r in payload["rows"]), f"{agent_id}: no priced rows"
    # Not a cost command → falls through to the adapter / CLI turn.
    assert handle_agent_cost_slash(m, "write a test", chat_session_id="42") is None


def test_codex_pinned_model_is_first_and_highlighted(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    monkeypatch.setattr(
        "api.agent_cost._session_pin",
        lambda aid, _sid, _cwd: ("gpt-6-luna", "low") if aid == "codex" else ("", ""),
    )
    md = run_agent_cost(_manifest("codex"), "", chat_session_id="42")
    rows = _pricing_payload(md)["rows"]
    assert rows[0]["model"] == "gpt-6-luna"
    assert rows[0]["active"] is True
    assert "effort `low`" in rows[0]["note"] and "pinned for this chat" in rows[0]["note"]
    assert sum(1 for r in rows if r.get("active")) == 1
    # OpenAI list rate, not the OpenRouter reseller row.
    assert rows[0]["input"] == 0.1
    assert (
        "Active: **GPT-6 Luna** `gpt-6-luna` · effort `low` (pinned for this chat) — "
        "$0.10 in / $0.50 out · ≈ $0.0159 / task"
    ) in md
    # Active row's task estimate uses the pinned effort (low → 0.6× output).
    assert rows[0]["task"] == pytest.approx(0.0159)
    assert "≈ / task: one typical agentic coding task" in md
    assert "active row at effort `low`" in md
    assert _pricing_payload(md)["task_basis"].startswith("~300K prompt tokens (80% cache hits)")
    # Remaining priced rows are cheapest-output first; unpriced rows last.
    rest = rows[1:]
    priced = [r["output"] for r in rest if r.get("output") is not None]
    assert priced == sorted(priced)
    assert rest[-1]["model"] == "codex-auto-review" and "input" not in rest[-1]


def test_starred_default_highlighted_when_no_pin(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    monkeypatch.setattr(
        "api.agent_harness.agent_defaults.get_starred_model",
        lambda aid: "muse-spark-1.3-contributor" if aid == "muse" else None,
    )
    rows = _pricing_payload(run_agent_cost(_manifest("muse"), ""))["rows"]
    assert rows[0]["model"] == "muse-spark-1.3-contributor" and rows[0]["active"]
    assert "starred default" in rows[0]["note"]


def test_cursor_groups_variants_and_highlights_pinned_variant(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    monkeypatch.setattr(
        "api.agent_cost._session_pin",
        lambda aid, _sid, _cwd: ("cursor-grok-4.6-high", "") if aid == "cursor" else ("", ""),
    )
    md = run_agent_cost(_manifest("cursor"), "", chat_session_id="7", cwd="E:/proj")
    rows = _pricing_payload(md)["rows"]
    by_model = {r["model"]: r for r in rows}
    grok = rows[0]
    assert grok["model"] == "grok-4.6" and grok["active"] is True
    assert grok["label"] == "Grok 4.6"
    assert "`cursor-grok-4.6-high`" in grok["note"]
    assert "low" in grok["variants"] and "+fast" in grok["variants"]
    assert grok["input"] == 2.0 and grok["output"] == 6.0
    # Three Opus 5 ids collapse into one row priced at the base model.
    assert by_model["claude-opus-5"]["input"] == 5.0
    assert "thinking" in by_model["claude-opus-5"]["variants"]
    # Auto / Composer have no public per-token rate — explained, not faked.
    assert "input" not in by_model["auto"] and "pool pricing" in by_model["auto"]["note"]
    assert "input" not in by_model["composer-2.5"] and "in-house" in by_model["composer-2.5"]["note"]


def test_claude_aliases_resolve(pricing_cache, no_pins, monkeypatch):
    rows = {r["model"]: r for r in _pricing_payload(run_agent_cost(_manifest("claude"), ""))["rows"]}
    assert rows["sonnet"]["input"] == 2.0 and "claude-sonnet-5" in rows["sonnet"]["note"]
    assert rows["haiku"]["input"] == 1.0


def test_deepseek_default_uses_provider_rate(pricing_cache, no_pins, monkeypatch):
    md = run_agent_cost(_manifest("deepseek"), "")
    rows = _pricing_payload(md)["rows"]
    assert rows[0]["model"] == "deepseek-v4-flash" and rows[0]["active"]
    assert rows[0]["input"] == 0.15  # DeepSeek list price, not the reseller's 0.098
    assert "CLI default" in rows[0]["note"]


def test_antigravity_effort_ids_share_base_rate(pricing_cache, no_pins, monkeypatch):
    rows = {r["model"]: r for r in _pricing_payload(run_agent_cost(_manifest("antigravity"), ""))["rows"]}
    assert rows["gemini-3.7-flash"]["input"] == 0.75
    assert "medium" in rows["gemini-3.7-flash"]["variants"]
    assert rows["gemini-3.1-pro-high"]["input"] == 2.0  # google -preview row, not poe


def test_hermes_local_model_is_free(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    rows = {r["model"]: r for r in _pricing_payload(run_agent_cost(_manifest("hermes"), ""))["rows"]}
    assert rows["qwen3-coder"]["input"] == 0.0 and "local backend" in rows["qwen3-coder"]["note"]
    assert rows["z-ai/glm-5.3-flash"]["active"] is True
    assert rows["deepseek/deepseek-v4.1-flash"]["input"] == 0.14


def test_opencode_first_class_rates_and_filter(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    m = _manifest("opencode")
    md = run_agent_cost(m, "")
    assert "reported by the harness" in md
    rows = _pricing_payload(md)["rows"]
    glm = rows[0]
    assert glm["model"] == "openrouter/z-ai/glm-5.3-flash" and glm["active"]
    assert glm["source"] == "cli" and glm["cache_read"] == 0.03
    # Default view hides the long tail; a filter searches the whole catalog.
    assert all(r["model"] != "opencode/big-pickle" for r in rows)
    filtered = _pricing_payload(run_agent_cost(m, "pickle"))["rows"]
    assert [r["model"] for r in filtered] == ["opencode/big-pickle"]
    assert filtered[0]["input"] == 0.0


def test_filter_and_all_args(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    table = build_cost_table(_manifest("codex"), active={"model": "", "effort": "", "source": ""}, args="sol")
    assert {r["model"] for r in table["rows"]} == {"gpt-6-sol", "gpt-5.6-sol"}
    assert table["terms"] == ["sol"]


def test_refresh_arg_refreshes_rates_and_catalog(pricing_cache, no_pins, monkeypatch):
    calls = {"pricing": 0, "catalog": []}
    monkeypatch.setattr(mp, "refresh_models_dev_pricing", lambda force=True: calls.__setitem__("pricing", calls["pricing"] + 1))

    def fake_codex(refresh=False):
        calls["catalog"].append(refresh)
        return {"models": [dict(m) for m in CODEX_MODELS]}

    monkeypatch.setattr("api.agent_harness.agents.codex.model_catalog.list_codex_catalog_models", fake_codex)
    run_agent_cost(_manifest("codex"), "refresh")
    assert calls["pricing"] == 1 and calls["catalog"] == [True]


# ---------------------------------------------------------------------------
# Kernel intercept
# ---------------------------------------------------------------------------


def _quiet_kernel(monkeypatch):
    monkeypatch.setattr("api.query_tracker.start_query_tracking", lambda *a, **k: "qid")
    monkeypatch.setattr("api.query_tracker.finish_query_tracking", lambda *a, **k: None)
    monkeypatch.setattr("api.query_tracker.get_query_tracker", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.unregister_execution", lambda *a, **k: None)


@pytest.mark.parametrize("prompt", ["/cost", "cost", "/pricing"])
def test_kernel_answers_cost_without_cli_turn(prompt, pricing_cache, no_pins, monkeypatch, tmp_path):
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentResult

    _quiet_kernel(monkeypatch)
    manifest = AgentManifest(
        id="codex",
        label="Codex",
        slash="/codex",
        models=["gpt-6-luna", "gpt-6-sol"],
        pricing_providers=["openai"],
        auto_install=True,
    )

    class _NoCliAdapter:
        def available(self):
            return False  # CLI missing: /cost must still answer (no install, no error)

        def resolve_cwd(self, project_path):
            return project_path

        def handle_meta(self, *_a, **_k):
            raise AssertionError("handle_meta must not see /cost")

        async def execute(self, *_a, **_k):
            raise AssertionError("/cost must not start a CLI turn")

    monkeypatch.setattr(kernel, "get_agent", lambda aid, project_path=None: (manifest, _NoCliAdapter()))
    monkeypatch.setattr(
        "api.agent_harness.installer.install_agent_cli",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("/cost must not install")),
    )
    monkeypatch.setattr(
        "api.agent_cost._load_catalog",
        lambda m, refresh: ([{"id": x, "label": x} for x in m.models], "manifest"),
    )
    out = kernel.run_agent_web_command("codex", prompt, 5, project_path=str(tmp_path))
    assert out["type"] == "codex_command"
    assert "Codex — model pricing" in out["response"]
    assert _pricing_payload(out["response"])["rows"][0]["model"] == "gpt-6-luna"


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------


def _extract_js(src: str, name: str) -> str:
    start = src.index(f"    function {name}(")
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _extract_js_const(src: str, name: str) -> str:
    # chat_slash.js owns these at top level (no indent); fall back to the
    # legacy 4-space IIFE indent when reading chat_page.js.
    start = src.find(f"const {name} = ")
    if start < 0:
        start = src.index(f"    const {name} = ")
    end = src.index("};", start) if src[src.index("=", start) + 2] == "{" else src.index("];", start)
    return src[start : end + 2]


def _run_node(tmp_path, script: str) -> str:
    driver = tmp_path / "driver.js"
    driver.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(driver)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_frontend_wires_pricing_renderer_and_styles():
    js = CHAT_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert "<cuttle_pricing>" in js
    assert "{{CUTTLE_PRICING_" in js
    assert "function renderCuttlePricingHtml(" in js
    assert "tr.is-active" in css and ".cuttle-pricing-badge" in css


# ---------------------------------------------------------------------------
# Measured $ / task (Epoch AI CursorBench / DeepSWE, SWE-bench bash-only)
# ---------------------------------------------------------------------------


def _epoch_zip() -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "cursorbench_external.csv",
            "Model version,Score,Reasoning level,Cost per task,Tokens per task,Release date\n"
            "grok-4.7_xhigh,0.463,Extra High,6.01,70141,2026-09-21\n"
            "muse-spark-1.3_minimal,0.243,,0.56,10620,2026-09-02\n"
            "Composer 2.5,0.277,,0.68,9000,2026-08-01\n"
            "no-cost_high,0.5,High,,1,2026-01-01\n",
        )
        z.writestr(
            "deepswe_external.csv",
            "Model version,Pass@1,Reasoning effort,Mean cost (USD),Mean output tokens,Release date\n"
            "gpt-5.4-2026-03-05_xhigh,0.518,xhigh,5.6525,1000,2026-03-05\n",
        )
        z.writestr("README.md", "unrelated")
    return buf.getvalue()


def test_parse_epoch_zip_normalizes_model_effort_and_score():
    from api.task_benchmarks import parse_epoch_zip

    rows = {(r["bench"], r["key"], r["effort"]): r for r in parse_epoch_zip(_epoch_zip())}
    grok = rows[("cursorbench", "grok47", "xhigh")]
    assert grok["model"] == "grok-4.7" and grok["cost"] == 6.01 and grok["score"] == 46.3
    # Effort from the version suffix when the column is empty.
    assert ("cursorbench", "musespark13", "minimal") in rows
    assert rows[("cursorbench", "composer25", "")]["cost"] == 0.68
    # Dated snapshot strips to the family key; Pass@1 fraction → percent.
    gpt = rows[("deepswe", "gpt54", "xhigh")]
    assert gpt["score"] == pytest.approx(51.8)
    assert not any(r["key"] == "nocost" for r in rows.values())


def test_parse_swebench_reads_verified_instance_cost():
    from api.task_benchmarks import parse_swebench

    data = {
        "leaderboards": [
            {"name": "Lite", "results": [{"instance_cost": 1, "tags": ["Model: x"]}]},
            {
                "name": "Verified",
                "results": [
                    {"instance_cost": 0.52, "resolved": 71.8, "reasoning_effort": "high",
                     "tags": ["Org: OpenAI", "Model: gpt-5.2-2025-12-11"], "date": "2025-12-11"},
                    {"instance_cost": None, "tags": ["Model: no-cost"]},
                ],
            },
        ]
    }
    rows = parse_swebench(data)
    assert len(rows) == 1
    assert rows[0]["key"] == "gpt52" and rows[0]["effort"] == "high" and rows[0]["score"] == 71.8


def test_model_key_and_effort_matching():
    from api.task_benchmarks import index_by_bench, match, model_key

    assert model_key("GPT-5.6 Luna") == model_key("gpt-5-6-luna") == model_key("gpt-5.6-luna")
    assert model_key("cursor-grok-4.6") == "grok46"
    assert model_key("openrouter/z-ai/glm-5.3-flash") == "glm53flash"
    assert model_key("gemini-3.1-pro-preview") == "gemini31pro"
    idx = index_by_bench(BENCH_ENTRIES)["cursorbench"]
    assert match(idx, ["grok-4.6"], "high")["cost"] == 5.2
    assert match(idx, ["grok-4.6"], "")["effort"] == "medium"  # default baseline
    assert match(idx, ["grok-4.6"], "xhigh")["effort"] == "medium"  # nearest available
    assert match(idx, ["nope", "claude-opus-5"], "")["cost"] == 6.94
    assert match(idx, ["nope"], "") is None


def test_load_entries_uses_fresh_cache_and_survives_fetch_failure(tmp_path, monkeypatch):
    import api.task_benchmarks as tb

    monkeypatch.undo()  # drop the autouse load_entries stub
    cache = tmp_path / "task_benchmarks_cache.json"
    monkeypatch.setattr(tb, "_cache_path", lambda: cache)
    monkeypatch.setattr(tb, "_mem", {})

    def boom():
        raise AssertionError("must not fetch")

    monkeypatch.setattr(tb, "fetch_entries", boom)
    cache.write_text(json.dumps({"version": tb._CACHE_VERSION, "fetched_at": 9_999_999_999.0,
                                 "entries": BENCH_ENTRIES[:1]}), encoding="utf-8")
    assert tb.load_entries()["entries"][0]["key"] == "grok46"

    cache.unlink()
    monkeypatch.setattr(tb, "_mem", {})
    monkeypatch.setattr(tb, "fetch_entries", lambda: {"entries": [], "errors": ["offline"]})
    assert tb.load_entries() == {"entries": [], "fetched_at": 0}


def test_cursor_prefers_cursorbench_and_pinned_effort(pricing_cache, no_pins, bench_data, monkeypatch):
    _fake_catalogs(monkeypatch)
    m = _manifest("cursor")
    assert m.pricing_benchmark == "cursorbench"
    md = run_agent_cost(m, "", chat_session_id="42", model_override="cursor-grok-4.6-high")
    payload = _pricing_payload(md)
    rows = {r["model"]: r for r in payload["rows"]}
    assert payload["bench"]["id"] == "cursorbench" and payload["bench"]["credit"] == "Epoch AI"
    # Effort baked into the Cursor id (``-high``) picks the high run.
    assert rows["grok-4.6"]["bench_cost"] == 5.2 and rows["grok-4.6"]["bench_effort"] == "high"
    # Grouped rows without a pin use the medium baseline, not a random variant.
    assert rows["claude-opus-5"]["bench_effort"] == "medium"
    assert rows["composer-2.5"]["bench_cost"] == 0.68
    assert "bench_cost" not in rows["auto"]
    assert "$5.20 / task on CursorBench (high, 40.4% solved)" in md
    assert "Measured $ / task: **CursorBench** via [Epoch AI]" in md
    assert "`/cost deepswe` / `/cost swebench`" in md


def test_benchmark_covering_active_model_wins(pricing_cache, no_pins, bench_data, monkeypatch):
    _fake_catalogs(monkeypatch)
    monkeypatch.setattr(
        "api.agent_cost._session_pin",
        lambda aid, _sid, _cwd: ("gpt-6-luna", "low") if aid == "codex" else ("", ""),
    )
    # CursorBench covers two Codex rows, DeepSWE only one — but that one is active.
    payload = _pricing_payload(run_agent_cost(_manifest("codex"), "", chat_session_id="42"))
    assert payload["bench"]["id"] == "deepswe"
    assert payload["rows"][0]["bench_cost"] == 0.4 and payload["rows"][0]["bench_effort"] == "low"


def test_cost_arg_forces_benchmark(pricing_cache, no_pins, bench_data, monkeypatch):
    _fake_catalogs(monkeypatch)
    payload = _pricing_payload(run_agent_cost(_manifest("codex"), "swebench", chat_session_id="42"))
    assert payload["bench"]["id"] == "swebench"
    rows = {r["model"]: r for r in payload["rows"]}
    assert rows["gpt-6-sol"]["bench_cost"] == 0.52
    assert all("bench_cost" not in r for m_id, r in rows.items() if m_id != "gpt-6-sol")
    # The benchmark name is not treated as a model filter.
    assert len(payload["rows"]) > 1


def test_no_benchmark_data_leaves_table_intact(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    md = run_agent_cost(_manifest("deepseek"), "", chat_session_id="42")
    assert "Measured $ / task" not in md
    assert "bench" not in _pricing_payload(md)


def test_estimate_task_cost_profile_math():
    from api.agent_cost import estimate_task_cost

    rates = {"input": 1.0, "output": 10.0, "cache_read": 0.1, "cache_write": 1.25}
    # 60K×1 + 240K×0.1 + 30K×1.25 + 15K×10, per 1M tokens.
    assert estimate_task_cost(rates) == pytest.approx(0.2715)
    assert estimate_task_cost(rates, "medium") == pytest.approx(0.2715)
    assert estimate_task_cost(rates, "high") == pytest.approx(0.3615)
    assert estimate_task_cost(rates, "low") < estimate_task_cost(rates)
    # No cache rates → cached / written tokens pay the full input price.
    assert estimate_task_cost({"input": 1.0, "output": 10.0}) == pytest.approx(0.48)
    assert estimate_task_cost({"input": 0.0, "output": 0.0}) == 0.0
    assert estimate_task_cost({"input": None, "output": 1.0}) is None
    assert estimate_task_cost({"input": 1.0}) is None


def test_task_column_on_every_priced_row(pricing_cache, no_pins, monkeypatch):
    _fake_catalogs(monkeypatch)
    md = run_agent_cost(_manifest("cursor"), "all", chat_session_id="42")
    rows = _pricing_payload(md)["rows"]
    priced = [r for r in rows if "input" in r]
    assert priced and all("task" in r for r in priced)
    assert all("task" not in r for r in rows if "input" not in r)
    # Non-active rows use the medium baseline.
    for r in priced:
        if not r.get("active"):
            from api.agent_cost import estimate_task_cost

            assert r["task"] == pytest.approx(estimate_task_cost(r))


@node_only
def test_pricing_renderer_highlights_active_row(tmp_path):
    src = CHAT_JS.read_text(encoding="utf-8")
    helpers = "\n".join(
        _extract_js(src, n)
        for n in ("escapeHtmlInline", "formatInlineMarkdown", "fmtPricingRate", "fmtPricingContext", "renderCuttlePricingHtml")
    )
    body = json.dumps(
        {
            "rows": [
                {"model": "gpt-6-luna", "label": "GPT-6 Luna", "input": 0.1, "output": 0.5, "cache_read": 0.01, "context": 400000, "task": 0.0159, "note": "active: effort `low`", "active": True},
                {"model": "gpt-6-sol", "label": "GPT-6 Sol", "input": 2, "output": 10, "task": 1.23},
                {"model": "qwen3-coder", "label": "<b>Local</b>", "input": 0, "output": 0, "task": 0},
                {"model": "codex-auto-review", "label": "codex-auto-review", "note": "no public rate found"},
            ],
            "task_basis": "~300K prompt tokens <x>",
        }
    )
    out = _run_node(
        tmp_path,
        helpers
        + "\nconst html = renderCuttlePricingHtml(" + json.dumps(body) + ");\n"
        + "console.log(JSON.stringify({html, empty: renderCuttlePricingHtml('not json')}));\n",
    )
    res = json.loads(out.strip().splitlines()[-1])
    html = res["html"]
    assert html.count('class="cuttle-pricing-row is-active"') == 1
    first_row = html.split("<tbody>", 1)[1].split("</tr>", 1)[0]
    assert "is-active" in first_row and "GPT-6 Luna" in first_row and "Active</span>" in first_row
    assert "$0.10" in html and "$0.50" in html and "$10.00" in html
    assert "Free" in html and "400K" in html and "—" in html
    assert "<code>low</code>" in html  # inline markdown in notes
    assert "<b>Local</b>" not in html and "&lt;b&gt;" in html  # labels are escaped
    assert res["empty"] == ""
    # ≈ / task column sits after Output, with the profile in the header tooltip.
    head = html.split("</thead>", 1)[0]
    assert head.index("Output") < head.index("≈ / task") < head.index("Cache read")
    assert "~300K prompt tokens &lt;x&gt;" in head
    assert '<td class="cuttle-pricing-num cuttle-pricing-task">$0.0159</td>' in first_row
    assert '<td class="cuttle-pricing-num cuttle-pricing-task">$1.23</td>' in html
    no_task = _run_node(
        tmp_path,
        helpers
        + "\nconsole.log(renderCuttlePricingHtml(JSON.stringify({rows:[{model:'a',input:1,output:2}]})));\n",
    )
    assert "≈ / task" not in no_task

    bench_body = json.dumps(
        {
            "rows": [
                {"model": "grok-4.6", "input": 2, "output": 6, "task": 0.44, "bench_cost": 5.2,
                 "bench_score": 40.4, "bench_effort": "high", "active": True},
                {"model": "auto", "note": "Cursor pool pricing"},
            ],
            "bench": {"id": "cursorbench", "label": "Cursor<Bench>", "credit": "Epoch AI"},
        }
    )
    bench_html = _run_node(
        tmp_path,
        helpers + "\nconsole.log(renderCuttlePricingHtml(" + json.dumps(bench_body) + "));\n",
    )
    bhead = bench_html.split("</thead>", 1)[0]
    assert bhead.index("Output") < bhead.index("$ / task") < bhead.index("Solved") < bhead.index("≈ / task")
    assert "Cursor&lt;Bench&gt;" in bhead and "Cursor<Bench>" not in bench_html
    assert "Epoch AI" in bhead
    active_row = bench_html.split("<tbody>", 1)[1].split("</tr>", 1)[0]
    assert "$5.20" in active_row and ">high</span>" in active_row and "40.4%" in active_row
    auto_row = bench_html.split("<tbody>", 1)[1].split("</tr>")[1]
    assert auto_row.count("—") >= 4  # unpriced / unbenchmarked cells


@node_only
def test_palette_offers_cost_for_every_harness_badge(tmp_path):
    # Registry tables + pure decision layer live in chat_slash.js (Phase 3
    # Slice 2); chip-gated assembly stays in chat_page.js.
    src = CHAT_JS.read_text(encoding="utf-8")
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    slash_mod = str(SLASH_JS).replace("\\", "\\\\")
    parts = [
        _extract_js_const(slash_src, "HARNESS_USAGE_SLASH_BY_AGENT"),
        _extract_js_const(slash_src, "HARNESS_COST_AGENT_LABELS"),
    ] + [
        _extract_js(src, n)
        for n in (
            "hasActiveMuseAgentChip",
            "hasActiveCodexAgentChip",
            "hasActiveHermesAgentChip",
            "hasActiveOpenCodeAgentChip",
            "hasActiveHarnessAgentChip",
            "harnessUsageSlashCommandsForPalette",
        )
    ]
    script = (
        f"const CuttleChatSlash = require({slash_mod!r});\n"
        "let slashCtx = {chat: {chips: []}, welcome: {chips: []}};\n"
        "let mode = 'cloud';\nfunction readInferenceMode() { return mode; }\n"
        "const { isStickyAgentChip, isStickyMuseAgentChip, isStickyCodexAgentChip, "
        "isStickyHermesAgentChip, isStickyOpenCodeAgentChip, harnessCostSlashCommand, "
        "isHarnessNestedCommandChip, composerChipAgentId } = CuttleChatSlash;\n"
        + "\n".join(parts)
        + """
const badges = {
  muse: {prefix: '/muse ', category: 'command'},
  codex: {prefix: '/codex ', category: 'codex'},
  hermes: {prefix: '/hermes ', category: 'command'},
  opencode: {prefix: '/opencode ', category: 'command'},
  claude: {prefix: '/claude ', category: 'command'},
  deepseek: {prefix: '/deepseek ', category: 'command'},
  antigravity: {prefix: '/antigravity ', category: 'command'},
};
const out = {};
for (const [agent, chip] of Object.entries(badges)) {
  slashCtx.chat.chips = [chip];
  const items = harnessUsageSlashCommandsForPalette();
  const cost = items.find((c) => c.prefix === '/cost');
  out[agent] = {
    prefixes: items.map((c) => c.prefix),
    category: cost && cost.category,
    owner: cost && composerChipAgentId(cost),
    nested: cost && isHarnessNestedCommandChip(cost),
    stickyLeak: cost && isStickyAgentChip(cost, agent),
  };
}
slashCtx.chat.chips = [];
out.none = harnessUsageSlashCommandsForPalette().length;
mode = 'local';
slashCtx.chat.chips = [badges.codex];
out.localCodex = harnessUsageSlashCommandsForPalette().length;
slashCtx.chat.chips = [badges.hermes];
out.localHermes = harnessUsageSlashCommandsForPalette().map((c) => c.prefix);
console.log(JSON.stringify(out));
"""
    )
    res = json.loads(_run_node(tmp_path, script).strip().splitlines()[-1])
    for agent in ("muse", "codex", "hermes", "opencode", "claude", "deepseek", "antigravity"):
        row = res[agent]
        assert "/cost" in row["prefixes"], agent
        assert row["category"] == f"{agent}-cmd"
        assert row["owner"] == agent
        assert row["nested"] is True
        assert row["stickyLeak"] is False
    # Harnesses with a /usage reporter keep it alongside /cost.
    for agent in ("muse", "codex", "hermes", "opencode"):
        assert res[agent]["prefixes"] == ["/usage", "/cost"]
    for agent in ("claude", "deepseek", "antigravity"):
        assert res[agent]["prefixes"] == ["/cost"]
    assert res["none"] == 0
    assert res["localCodex"] == 0
    assert res["localHermes"] == ["/usage", "/cost"]


def test_cursor_palette_has_cost_and_sendable_gate():
    js = CHAT_JS.read_text(encoding="utf-8")
    slash_js = SLASH_JS.read_text(encoding="utf-8")
    start = slash_js.find("const CURSOR_AGENT_SLASH_COMMANDS = [")
    block = slash_js[start : slash_js.find("];", start)]
    assert "prefix: '/cost'" in block and "category: 'cursor-cmd'" in block
    sendable = js[js.find("function isSendableComposerMessage(") :][:2400]
    assert "usage|cost" in sendable
    # Never a global sticky/agent command.
    base = slash_js[slash_js.find("const SLASH_COMMANDS = [") : slash_js.find("const CURSOR_AGENT_SLASH_COMMANDS")]
    assert "prefix: '/cost'" not in base
