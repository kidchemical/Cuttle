"""Offline policy tests for CH-000419 agent defaults (no LLM turns).

Covers, without spawning any CLI:

* resolution order: session pin → explicit override → starred → CLI default
* starred model/effort CRUD per agent
* kernel never injects ``manifest.default_model``
* manifests that used to override CLI defaults are now empty
* cursor starred model + auto fallback (effort stays baked into the model)
* opencode ``--variant`` from live catalog (+ optional manifest overlays)
* canonical badge meta shape + drift warning log
"""

import pytest

from api.agent_harness import agent_defaults as ad
from api import chat_warnings as cw


class _FakeSettings:
    def __init__(self):
        self.store = {}

    def get_setting(self, key, default=None):
        return self.store.get(key, default)

    def set_setting(self, key, value):
        self.store[key] = value
        return True


@pytest.fixture()
def fake_settings(monkeypatch):
    fake = _FakeSettings()
    monkeypatch.setattr(ad, "_settings", lambda: fake)
    return fake


def test_resolution_order_session_beats_all(fake_settings):
    fake_settings.store[ad.MODELS_KEY] = {"muse": "starred-id"}
    model, source = ad.resolve_effective_model(
        "muse",
        session_model="session-id",
        kernel_override="override-id",
        cli_default="cli-id",
    )
    assert (model, source) == ("session-id", ad.SOURCE_SESSION)


def test_resolution_order_override_beats_starred(fake_settings):
    fake_settings.store[ad.MODELS_KEY] = {"muse": "starred-id"}
    model, source = ad.resolve_effective_model(
        "muse",
        kernel_override="override-id",
        cli_default="cli-id",
    )
    assert (model, source) == ("override-id", ad.SOURCE_OVERRIDE)


def test_resolution_order_starred_beats_cli(fake_settings):
    fake_settings.store[ad.MODELS_KEY] = {"muse": "starred-id"}
    model, source = ad.resolve_effective_model("muse", cli_default="cli-id")
    assert (model, source) == ("starred-id", ad.SOURCE_STARRED)


def test_resolution_order_cli_default_last(fake_settings):
    model, source = ad.resolve_effective_model("muse", cli_default="cli-id")
    assert (model, source) == ("cli-id", ad.SOURCE_CLI_DEFAULT)


def test_resolution_order_nothing_means_cli_decides(fake_settings):
    model, source = ad.resolve_effective_model("muse")
    assert model is None
    assert source == ad.SOURCE_CLI_DEFAULT


def test_starred_model_crud(fake_settings):
    assert ad.get_starred_model("muse") is None
    assert ad.set_starred_model("muse", "muse-spark-1.3-contributor") == "muse-spark-1.3-contributor"
    assert ad.get_starred_model("MUSE") == "muse-spark-1.3-contributor"
    assert ad.set_starred_model("muse", "") is None
    assert ad.get_starred_model("muse") is None


def test_starred_effort_crud_and_normalization(fake_settings):
    assert ad.set_starred_effort("muse", "HIGH") == "high"
    assert ad.get_starred_effort("muse") == "high"
    assert ad.set_starred_effort("muse", "") is None


def test_effort_resolution_order(fake_settings):
    fake_settings.store[ad.EFFORTS_KEY] = {"muse": "high"}
    effort, source = ad.resolve_effective_effort("muse", session_effort="low")
    assert (effort, source) == ("low", ad.SOURCE_SESSION)
    effort, source = ad.resolve_effective_effort("muse")
    assert (effort, source) == ("high", ad.SOURCE_STARRED)
    effort, source = ad.resolve_effective_effort("cursor")
    assert (effort, source) == (None, ad.SOURCE_NONE)


def test_badge_meta_is_generic_only():
    meta = ad.badge_meta("muse", "mid-1", ad.SOURCE_STARRED, "high", ad.SOURCE_SESSION)
    assert meta["agent_model"] == "mid-1"
    assert meta["model_source"] == ad.SOURCE_STARRED
    assert meta["agent_effort"] == "high"
    assert meta["effort_source"] == ad.SOURCE_SESSION
    # Per-agent duplicates are never emitted (CH-000419 #2).
    assert "muse_model" not in meta
    assert "muse_effort" not in meta
    assert "hermes_model" not in meta
    assert "opencode_model" not in meta


def test_kernel_does_not_inject_manifest_default():
    import inspect

    from api.agent_harness import kernel

    src = inspect.getsource(kernel.run_agent_web_command)
    assert "manifest.default_model or None" not in src


def test_manifests_do_not_override_cli_defaults():
    from api.agent_harness.catalog import get_agent

    for aid in ("muse", "opencode", "claude", "antigravity", "deepseek"):
        pair = get_agent(aid)
        assert pair is not None, aid
        manifest, _adapter = pair
        assert manifest.default_model == "", aid


def test_cursor_manifest_keeps_auto_cli_default():
    from api.agent_harness.catalog import get_agent

    manifest, _adapter = get_agent("cursor")
    assert manifest.default_model == "auto"


def test_cursor_resolve_prefers_session_then_starred(fake_settings):
    from api.agent_harness.agents.cursor.adapter import _resolve_cursor_model

    assert _resolve_cursor_model(
        slash_preferred="slash-id", session_model="s", kernel_model="k"
    ) == "slash-id"
    assert _resolve_cursor_model(
        slash_preferred=None, session_model="session-id", kernel_model="kernel-id"
    ) == "session-id"
    fake_settings.store[ad.MODELS_KEY] = {"cursor": "starred-id"}
    assert _resolve_cursor_model(
        slash_preferred=None, session_model=None, kernel_model="auto"
    ) == "starred-id"
    assert _resolve_cursor_model(
        slash_preferred=None, session_model=None, kernel_model=None
    ) == "starred-id"
    fake_settings.store[ad.MODELS_KEY] = {}
    assert _resolve_cursor_model(
        slash_preferred=None, session_model=None, kernel_model="auto"
    ) == "auto"


def test_opencode_variant_guard(monkeypatch):
    from api.agent_harness.agents.opencode import adapter as oc
    from api.agent_harness.catalog import get_agent, reload_catalog

    reload_catalog()
    pair = get_agent("opencode")
    assert pair is not None
    # Stale GLM ban removed — catalog + optional overlays only.
    assert not any(
        getattr(rule, "contains", "").lower() == "glm"
        and rule.supports.get("variant") is False
        for rule in (pair[0].model_capabilities or ())
    )

    monkeypatch.setattr(
        "api.agent_harness.agents.opencode.model_catalog.catalog_supports_variant",
        lambda model: {
            "openrouter/z-ai/glm-5.3-flash": True,
            "openai/gpt-4o-mini": True,
            "openrouter/example/no-variants": False,
        }.get((model or "").strip().lower()),
    )
    assert oc.opencode_supports_variant("openrouter/z-ai/glm-5.3-flash") is True
    assert oc.opencode_supports_variant("openai/gpt-4o-mini") is True
    assert oc.opencode_supports_variant("openrouter/example/no-variants") is False
    assert oc.opencode_supports_variant("") is True
    assert oc.opencode_supports_variant(None) is True

    # Manifest overlay still wins when present.
    from api.agent_harness.model_capabilities import ModelCapabilityRule
    from api.agent_harness.types import AgentManifest

    fake = AgentManifest(
        id="opencode",
        label="OpenCode",
        slash="/opencode",
        model_capabilities=(
            ModelCapabilityRule(contains="no-variants", supports={"variant": True}),
        ),
    )
    monkeypatch.setattr(
        "api.agent_harness.catalog.get_agent",
        lambda aid, project_path=None: (fake, object()) if aid == "opencode" else None,
    )
    assert oc.opencode_supports_variant("openrouter/example/no-variants") is True


def test_model_capabilities_overlay_file(tmp_path):
    from api.agent_harness.model_capabilities import (
        load_model_capability_rules,
        model_supports,
    )
    from api.agent_harness.types import AgentManifest

    agent_dir = tmp_path / "demo"
    agent_dir.mkdir()
    (agent_dir / "models").mkdir()
    (agent_dir / "model_capabilities.yaml").write_text(
        "- contains: glm\n  supports_variant: false\n",
        encoding="utf-8",
    )
    (agent_dir / "models" / "openai__gpt-4o.yaml").write_text(
        "supports_variant: true\n",
        encoding="utf-8",
    )
    rules = load_model_capability_rules(
        agent_dir,
        manifest_raw={"model_capabilities": [{"prefix": "x-ai/", "supports_variant": False}]},
    )
    manifest = AgentManifest(
        id="demo",
        label="Demo",
        slash="/demo",
        model_capabilities=rules,
    )
    assert model_supports(manifest, "openrouter/z-ai/glm-5.3-flash", "variant") is False
    assert model_supports(manifest, "x-ai/grok", "variant") is False
    assert model_supports(manifest, "openai/gpt-4o", "variant") is True
    assert model_supports(manifest, "openai/gpt-4o-mini", "variant") is True


def test_manifest_capabilities_cursor_has_no_separate_effort():
    from api.agent_harness.catalog import get_agent

    manifest, _adapter = get_agent("cursor")
    assert manifest.supports_model_pin is True
    assert manifest.supports_effort is False


def test_manifest_capabilities_default_full_support():
    from api.agent_harness.catalog import get_agent

    for aid in ("muse", "hermes", "opencode", "codex"):
        pair = get_agent(aid)
        assert pair is not None, aid
        manifest, _adapter = pair
        assert manifest.supports_model_pin is True, aid
        assert manifest.supports_effort is True, aid


def test_agent_capabilities_reader():
    caps = ad.agent_capabilities("cursor")
    assert caps == {"supports_model_pin": True, "supports_effort": False}
    caps = ad.agent_capabilities("muse")
    assert caps == {"supports_model_pin": True, "supports_effort": True}
    # Unknown drop-in ids default to full support (never lock out new agents).
    caps = ad.agent_capabilities("some-future-agent")
    assert caps == {"supports_model_pin": True, "supports_effort": True}


def test_agent_capabilities_exposed_in_public_dict():
    from api.agent_harness.catalog import get_agent

    manifest, _adapter = get_agent("cursor")
    public = manifest.to_public_dict()
    assert public["supports_model_pin"] is True
    assert public["supports_effort"] is False


def test_cursor_models_endpoint_prefers_starred_without_session(monkeypatch):
    import api.cursor_agent_commands as cac
    import api.web_chat_api as wca

    fake = _FakeSettings()
    monkeypatch.setattr(ad, "_settings", lambda: fake)
    monkeypatch.setattr(
        cac, "list_cursor_agent_models", lambda: [{"id": "auto", "label": "Auto"}]
    )
    ad.set_starred_model("cursor", "cursor-grok-4.6-high")
    client = wca.app.test_client()
    try:
        starred = client.get("/api/cursor-agent/models")
        assert starred.status_code == 200
        body = starred.get_json()
        assert body["success"] is True
        assert body["preferredModel"] == "cursor-grok-4.6-high"
        assert body["preferredSource"] == "starred"
    finally:
        ad.set_starred_model("cursor", "")
    bare = client.get("/api/cursor-agent/models")
    assert bare.get_json()["preferredModel"] == "auto"


def test_agent_defaults_post_rejects_capability_violations(monkeypatch, owner_session):
    import api.web_chat_api as wca

    fake = _FakeSettings()
    monkeypatch.setattr(ad, "_settings", lambda: fake)
    client = wca.app.test_client()
    anon = client.post("/api/agent-defaults/muse", json={"starred_effort": "high"})
    assert anon.status_code == 401
    owner_session.sign_in(client)
    denied = client.post("/api/agent-defaults/cursor", json={"starred_effort": "high"})
    assert denied.status_code == 400
    assert denied.get_json()["success"] is False
    allowed = client.post("/api/agent-defaults/muse", json={"starred_effort": "high"})
    assert allowed.status_code == 200
    assert allowed.get_json()["success"] is True
    assert ad.get_starred_effort("muse") == "high"


def test_drift_warning_logged_and_fetched():
    cw.clear_warnings("sess-1")
    assert cw.get_warnings("sess-1") == []
    entry = cw.record_model_drift(
        "sess-1", agent_id="muse", expected="a", actual="b", source="override"
    )
    assert entry is not None
    rows = cw.get_warnings("sess-1")
    assert len(rows) == 1
    assert rows[0]["kind"] == "model_drift"
    assert rows[0]["expected"] == "a"
    # Same expected/actual → no entry.
    assert cw.record_model_drift(
        "sess-1", agent_id="muse", expected="a", actual="a"
    ) is None
    assert len(cw.get_warnings("sess-1")) == 1
    cw.clear_warnings("sess-1")
    assert cw.get_warnings("sess-1") == []
