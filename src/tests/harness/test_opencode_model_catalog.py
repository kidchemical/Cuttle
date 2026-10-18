"""OpenCode live model catalog (opencode models → slash palette)."""

from __future__ import annotations

import pytest


@pytest.fixture
def catalog_cache(tmp_path, monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    cache = tmp_path / "opencode_models_cache.json"
    monkeypatch.setattr(mc, "_cache_path", lambda: cache)
    monkeypatch.setattr(mc, "_CACHE_TTL_SEC", 3600)
    return cache


_VERBOSE_SAMPLE = """
openrouter/deepseek/deepseek-v4.1-flash
{
  "id": "deepseek/deepseek-v4.1-flash",
  "providerID": "openrouter",
  "name": "DeepSeek V4.1 Flash",
  "capabilities": {"reasoning": true, "toolcall": true},
  "variants": {
    "low": {"reasoning": {"effort": "low"}},
    "high": {"reasoning": {"effort": "high"}}
  }
}
openrouter/z-ai/glm-5.3-flash
{
  "id": "z-ai/glm-5.3-flash",
  "providerID": "openrouter",
  "name": "GLM-5.3-Flash",
  "capabilities": {"reasoning": true},
  "variants": {
    "low": {"reasoning": {"effort": "low"}},
    "max": {"reasoning": {"effort": "max"}}
  }
}
openrouter/example/no-variants
{
  "id": "example/no-variants",
  "providerID": "openrouter",
  "name": "No Variants",
  "capabilities": {"reasoning": false, "temperature": true}
}
"""


def test_parse_models_stdout_filters_noise():
    from api.agent_harness.agents.opencode.model_catalog import _parse_models_stdout

    text = """
Available models
openrouter/deepseek/deepseek-v4.1-flash
openrouter/z-ai/glm-5.3-flash
not a model line
openai/gpt-4o-mini
"""
    rows = _parse_models_stdout(text)
    ids = [r["id"] for r in rows]
    assert ids == [
        "openrouter/deepseek/deepseek-v4.1-flash",
        "openrouter/z-ai/glm-5.3-flash",
        "openai/gpt-4o-mini",
    ]
    assert "Deepseek" in rows[0]["label"] or "V4.1" in rows[0]["label"]


def test_parse_verbose_bakes_supports_variant():
    from api.agent_harness.agents.opencode.model_catalog import _parse_models_stdout

    rows = _parse_models_stdout(_VERBOSE_SAMPLE)
    by_id = {r["id"]: r for r in rows}
    assert by_id["openrouter/z-ai/glm-5.3-flash"]["supports_variant"] is True
    assert by_id["openrouter/deepseek/deepseek-v4.1-flash"]["supports_variant"] is True
    assert by_id["openrouter/example/no-variants"]["supports_variant"] is False
    assert "low" in by_id["openrouter/z-ai/glm-5.3-flash"]["variant_levels"]
    assert by_id["openrouter/z-ai/glm-5.3-flash"]["label"] == "GLM-5.3-Flash"


def test_list_catalog_uses_cli_and_caches(catalog_cache, monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    calls = {"n": 0}

    def fake_run(*, refresh=False):
        calls["n"] += 1
        return (mc._parse_models_stdout(_VERBOSE_SAMPLE), None)

    monkeypatch.setattr(mc, "_run_opencode_models", fake_run)
    monkeypatch.setattr(
        mc,
        "_curated_favorites",
        lambda: [
            {
                "id": "openrouter/z-ai/glm-5.3-flash",
                "label": "GLM 5.3 Flash (OpenRouter)",
                "description": "Curated OpenCode favorite",
                "favorite": "1",
            }
        ],
    )

    first = mc.list_opencode_catalog_models()
    assert calls["n"] == 1
    assert first["source"] == "cli"
    assert first["count"] >= 2
    ids = [m["id"] for m in first["models"]]
    assert ids[0] == "openrouter/z-ai/glm-5.3-flash"  # favorite first
    assert "openrouter/deepseek/deepseek-v4.1-flash" in ids
    glm = next(m for m in first["models"] if "glm-5.3-flash" in m["id"])
    assert glm["supports_variant"] is True
    assert catalog_cache.is_file()

    second = mc.list_opencode_catalog_models()
    assert calls["n"] == 1  # cache hit
    assert second["source"] == "cache"

    q = mc.list_opencode_catalog_models(q="deepseek v4.1")
    assert q["count"] == 1
    assert q["models"][0]["id"] == "openrouter/deepseek/deepseek-v4.1-flash"

    refreshed = mc.list_opencode_catalog_models(refresh=True)
    assert calls["n"] == 2
    assert refreshed["source"] == "cli_refresh"

    assert mc.catalog_supports_variant("openrouter/z-ai/glm-5.3-flash") is True
    assert mc.catalog_supports_variant("openrouter/example/no-variants") is False
    assert mc.catalog_variant_levels("openrouter/z-ai/glm-5.3-flash") == ["low", "max"]
    assert mc.catalog_variant_levels("openrouter/example/no-variants") == []


def test_list_catalog_falls_back_to_favorites(catalog_cache, monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    monkeypatch.setattr(
        mc, "_run_opencode_models", lambda *, refresh=False: ([], "CLI missing")
    )
    monkeypatch.setattr(
        mc,
        "_curated_favorites",
        lambda: [
            {
                "id": "openai/gpt-4o-mini",
                "label": "GPT-4o mini",
                "description": "Curated",
                "favorite": "1",
            }
        ],
    )
    out = mc.list_opencode_catalog_models()
    assert out["source"] == "favorites_fallback"
    assert out["error"] == "CLI missing"
    assert out["models"][0]["id"] == "openai/gpt-4o-mini"


_TRUNCATED_MID_OBJECT = """\
openrouter/deepseek/deepseek-v4.1-flash
{
  "id": "deepseek/deepseek-v4.1-flash",
  "providerID": "openrouter",
  "name": "DeepSeek V4.1 Flash",
  "capabilities": {"reasoning": true, "toolcall": true},
  "variants": {}
}
openrouter/z-ai/glm-5.3-flash
{
  "id": "z-ai/glm-5.3-flash",
  "providerID": "openrouter",
  "audio": false,
"""

_VERBOSE_ONE_MODEL = """\
openrouter/deepseek/deepseek-v4.1-flash
{
  "id": "deepseek/deepseek-v4.1-flash",
  "providerID": "openrouter",
  "name": "DeepSeek V4.1 Flash",
  "capabilities": {"reasoning": true, "toolcall": true},
  "variants": {}
}
"""

_PLAIN_THREE_IDS = """\
Available models
openrouter/deepseek/deepseek-v4.1-flash
openrouter/z-ai/glm-5.3-flash
openrouter/example/no-variants
"""


class _Proc:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _mock_run(monkeypatch, script):
    """Serve canned ``opencode models`` outputs; record argv shapes seen."""
    from api.agent_harness.agents.opencode import model_catalog as mc

    seen = []
    it = iter(script)

    def fake_run(cmd, **kwargs):
        seen.append([c for c in cmd if str(c).startswith("-")])
        return next(it)

    monkeypatch.setattr(
        "api.agent_harness.agents.opencode.adapter.opencode_executable",
        lambda: "/fake/opencode",
    )
    monkeypatch.setattr(mc.subprocess, "run", fake_run)
    return seen


def test_run_rejects_mid_object_truncation(monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    seen = _mock_run(monkeypatch, [_Proc(_TRUNCATED_MID_OBJECT)])
    rows, err = mc._run_opencode_models(refresh=True)
    assert rows == []
    assert err is not None and "mid-object" in err
    # Rejected on the end marker alone — no second CLI call needed.
    assert seen == [["--verbose", "--refresh"]]


def test_run_rejects_boundary_exact_cut_via_plain_count(monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    seen = _mock_run(
        monkeypatch, [_Proc(_VERBOSE_ONE_MODEL), _Proc(_PLAIN_THREE_IDS)]
    )
    rows, err = mc._run_opencode_models(refresh=True)
    assert rows == []
    assert err is not None and "plain" in err
    assert seen == [["--verbose", "--refresh"], []]


def test_run_accepts_complete_stream(monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    _mock_run(monkeypatch, [_Proc(_VERBOSE_SAMPLE), _Proc(_PLAIN_THREE_IDS)])
    rows, err = mc._run_opencode_models(refresh=True)
    assert err is None
    assert [r["id"] for r in rows] == [
        "openrouter/deepseek/deepseek-v4.1-flash",
        "openrouter/z-ai/glm-5.3-flash",
        "openrouter/example/no-variants",
    ]


def test_run_accepts_complete_stream_when_plain_check_fails(monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    _mock_run(monkeypatch, [_Proc(_VERBOSE_SAMPLE), _Proc("", returncode=1)])
    rows, err = mc._run_opencode_models(refresh=True)
    assert err is None
    assert len(rows) == 3


def test_refresh_never_overwrites_good_cache_with_partial(catalog_cache, monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as mc

    full = mc._parse_models_stdout(_VERBOSE_SAMPLE)
    mc._write_cache(full, refreshed=True)
    before = catalog_cache.read_bytes()

    _mock_run(monkeypatch, [_Proc(_TRUNCATED_MID_OBJECT)])
    out = mc.list_opencode_catalog_models(refresh=True)

    assert catalog_cache.read_bytes() == before
    assert out["error"] is not None and "mid-object" in out["error"]
    ids = [m["id"] for m in out["models"]]
    assert "openrouter/deepseek/deepseek-v4.1-flash" in ids


def test_opencode_model_slash_refresh(monkeypatch):
    from api.agent_harness.agents.opencode.adapter import _handle_opencode_model_slash

    monkeypatch.setattr(
        "api.agent_harness.agents.opencode.model_catalog.refresh_opencode_catalog",
        lambda: {"count": 444, "source": "cli_refresh", "error": None},
    )
    result = _handle_opencode_model_slash(
        "model refresh", "99", "openrouter/z-ai/glm-5.3-flash"
    )
    assert result is not None
    assert result.success is True
    assert "444" in result.output
    assert "palette" in result.output.lower()
