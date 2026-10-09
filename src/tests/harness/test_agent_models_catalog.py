"""Shared live model-catalog store used by /cost, Settings and the Router."""

from __future__ import annotations

from api.agent_harness import agent_models as am


def test_load_agent_catalog_prefers_live_rows(monkeypatch):
    monkeypatch.setitem(
        am._CATALOG_LOADERS,
        "demo",
        lambda refresh: ([{"id": "live-1", "label": "Live"}], "cli"),
    )
    rows, source = am.load_agent_catalog("demo", ["m1"], False)
    assert [r["id"] for r in rows] == ["live-1"] and source == "cli"


def test_load_agent_catalog_falls_back_to_manifest(monkeypatch):
    monkeypatch.setitem(am._CATALOG_LOADERS, "demo2", lambda refresh: ([], "cli"))
    rows, source = am.load_agent_catalog("demo2", ["m1", "m2"], False)
    assert [r["id"] for r in rows] == ["m1", "m2"] and source == "manifest"
    # No loader (drop-in) → manifest snapshot, not an error.
    rows, source = am.load_agent_catalog("nope", ["m9"], False)
    assert [r["id"] for r in rows] == ["m9"] and source == "manifest"


def test_load_agent_catalog_survives_a_failing_loader(monkeypatch):
    def boom(refresh):
        raise RuntimeError("cli exploded")

    monkeypatch.setitem(am._CATALOG_LOADERS, "demo3", boom)
    rows, source = am.load_agent_catalog("demo3", ["fall"], False)
    assert [r["id"] for r in rows] == ["fall"] and source == "manifest"


def test_bulk_and_row_helpers(monkeypatch):
    monkeypatch.setitem(
        am._CATALOG_LOADERS,
        "a",
        lambda refresh: ([{"id": "x", "efforts": ["Low", "high"]}], "cli"),
    )
    monkeypatch.setitem(
        am._CATALOG_LOADERS, "b", lambda refresh: ([{"id": "y", "label": "Y"}], "cli")
    )
    out = am.list_agent_models_bulk(["a", "b"], fallbacks={})
    assert am.catalog_model_ids(out["a"]["models"]) == ["x"]
    assert am.catalog_model_efforts(out["a"]["models"]) == {"x": ["low", "high"]}
    assert am.catalog_model_ids(out["b"]["models"]) == ["y"]


def _stub_catalog(monkeypatch):
    import api.agent_harness.catalog as catalog_mod

    monkeypatch.setattr(
        catalog_mod,
        "public_catalog",
        lambda project_path=None: [
            {
                "id": "opencode",
                "label": "OpenCode",
                "available": True,
                "supports_model_pin": True,
                "models": ["curated-1"],
                "model_efforts": {},
                "efforts": [],
            }
        ],
    )
    monkeypatch.setattr(catalog_mod, "discovery_roots", lambda project_path=None: [])


def test_agents_endpoint_live_models_enriches_settings(monkeypatch):
    from api import web_chat_api as w

    _stub_catalog(monkeypatch)
    monkeypatch.setattr(
        am,
        "list_agent_models_bulk",
        lambda ids, fallbacks=None, refresh=False: {
            "opencode": {
                "models": [{"id": "openrouter/deepseek/v9", "label": "V9"}],
                "source": "`opencode models --verbose`",
            },
        },
    )
    client = w.app.test_client()
    body = client.get("/api/agents?live_models=1").get_json()
    assert body["success"] and body["live_models"] is True
    oc = next(a for a in body["agents"] if a["id"] == "opencode")
    assert oc["models"] == ["openrouter/deepseek/v9"]
    assert oc["models_source"]


def test_agents_endpoint_without_flag_spawns_no_catalog(monkeypatch):
    from api import web_chat_api as w

    _stub_catalog(monkeypatch)

    def _boom(*_a, **_k):
        raise AssertionError("plain /api/agents must not consult live catalogs")

    monkeypatch.setattr(am, "list_agent_models_bulk", _boom)
    client = w.app.test_client()
    body = client.get("/api/agents").get_json()
    assert body["success"] and body["live_models"] is False


def test_opencode_manifest_exposes_effort_levels():
    """Settings/Router read `efforts`; without it the effort picker is hidden."""
    from api.agent_harness.agents.opencode.adapter import OPENCODE_REASONING_EFFORTS
    from api.agent_harness.agent_defaults import agent_capabilities
    from api.agent_harness.catalog import get_agent

    manifest, _ = get_agent("opencode")
    assert agent_capabilities("opencode")["supports_effort"] is True
    assert list(manifest.efforts) == list(OPENCODE_REASONING_EFFORTS)
