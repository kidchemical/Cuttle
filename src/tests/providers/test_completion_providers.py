"""Cheap-completion provider registry (``src/api/completion_providers.py``).

This module replaced two dead Settings dropdowns. ``preferred_llm_model`` was
POSTed by the page and read by nothing; ``preferred_tools_ollama_model`` was
read only by ``/api/llm-request``, a pipeline endpoint no frontend calls. So
these tests are mostly about the resolution order and the "writes are read"
property that made the old controls dead in the first place.

Precedence, asserted below: explicit argument → env override → Settings →
provider default.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api import completion_providers as cp  # noqa: E402


@pytest.fixture
def store(monkeypatch):
    """In-memory settings_manager stand-in."""
    data: dict = {}

    class _Manager:
        def get_setting(self, key, default=None):
            return data.get(key, default)

        def set_setting(self, key, value):
            data[key] = value
            return True

    monkeypatch.setattr(cp, "_settings", lambda: _Manager())
    return data


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    for name in (
        "OPENAI_API_KEY", "API_KEY", "ANTHROPIC_API_KEY",
        "CUTTLE_LLM_OPENAI_MODEL", "CUTTLE_LLM_ANTHROPIC_MODEL", "OLLAMA_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


# ── registry shape ────────────────────────────────────────────────────────


def test_registry_exposes_the_three_real_backends():
    ids = [p["id"] for p in cp.list_providers()]
    assert ids == list(cp.DEFAULT_ORDER)


def test_provider_rows_carry_what_the_ui_needs():
    for row in cp.list_providers():
        for key in (
            "id", "label", "credential_env", "default_model",
            "suggested_models", "configured", "credential_present",
        ):
            assert key in row, f"{row.get('id')} missing {key}"


def test_missing_credential_reads_as_not_configured(monkeypatch):
    openai = cp.get_provider("openai")
    assert openai.credential_present() is False
    assert openai.configured() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert cp.get_provider("openai").configured() is True


def test_legacy_api_key_name_still_counts(monkeypatch):
    """API_KEY was always an accepted OpenAI alias; keep it working."""
    monkeypatch.setenv("API_KEY", "sk-legacy")
    assert cp.get_provider("openai").credential_present() is True


def test_local_needs_no_credential():
    """Local must read as configured on a fresh install with no keys at all."""
    assert cp.get_provider("local").configured() is True
    assert cp.get_provider("local").needs_credential is False


# ── model resolution order ────────────────────────────────────────────────


def test_default_model_when_nothing_is_configured():
    assert cp.resolve_model("openai") == "gpt-4o-mini"
    assert cp.resolve_model("anthropic") == "claude-haiku-4-5-20251001"


def test_settings_win_over_the_bundled_default(store):
    cp.set_provider_model("openai", "gpt-4.1-mini")
    assert cp.resolve_model("openai") == "gpt-4.1-mini"


def test_env_beats_settings(store, monkeypatch):
    """An operator pinning a model in .env must not be overruled by the UI."""
    cp.set_provider_model("openai", "gpt-4.1-mini")
    monkeypatch.setenv("CUTTLE_LLM_OPENAI_MODEL", "gpt-4o")
    assert cp.resolve_model("openai") == "gpt-4o"


def test_explicit_argument_beats_everything(store, monkeypatch):
    cp.set_provider_model("openai", "gpt-4.1-mini")
    monkeypatch.setenv("CUTTLE_LLM_OPENAI_MODEL", "gpt-4o")
    assert cp.resolve_model("openai", "gpt-4.1") == "gpt-4.1"


def test_local_resolves_empty_when_unset():
    """Empty means 'let core.local_llm decide' — it knows llama.cpp vs Ollama."""
    assert cp.resolve_model("local") == ""


def test_arbitrary_model_ids_are_accepted(store):
    """The old allowlist rejected every current model; nothing gates ids now."""
    cp.set_provider_model("openai", "gpt-5.2-codex-preview")
    assert cp.resolve_model("openai") == "gpt-5.2-codex-preview"


def test_unknown_provider_resolves_to_nothing():
    assert cp.resolve_model("not-a-provider") == ""
    assert cp.get_provider("not-a-provider") is None


# ── pinning ───────────────────────────────────────────────────────────────


def test_pin_puts_the_provider_first_without_dropping_the_others(store):
    assert cp.resolve_order() == ["openai", "anthropic", "local"]
    assert cp.set_preferred_provider("anthropic") is True
    assert cp.preferred_provider_id() == "anthropic"
    assert cp.resolve_order() == ["anthropic", "openai", "local"]


def test_clear_pin_returns_to_registry_order(store):
    cp.set_preferred_provider("local")
    assert cp.resolve_order()[0] == "local"
    assert cp.set_preferred_provider("auto") is True
    assert cp.preferred_provider_id() is None
    assert cp.resolve_order() == ["openai", "anthropic", "local"]


def test_explicit_chain_ignores_the_pin(store):
    """A caller pinning a hop must not have another provider injected."""
    cp.set_preferred_provider("anthropic")
    assert cp.resolve_order("openai") == ["openai"]


def test_unknown_pin_is_refused(store):
    assert cp.set_preferred_provider("nope") is False
    assert cp.preferred_provider_id() is None


def test_switching_provider_keeps_the_other_model_map(store):
    cp.set_provider_model("openai", "gpt-4.1-mini")
    cp.set_provider_model("anthropic", "claude-haiku-4-5-20251001")
    cp.set_preferred_provider("anthropic")
    assert cp.resolve_model("openai") == "gpt-4.1-mini"
    assert cp.resolve_model("anthropic") == "claude-haiku-4-5-20251001"


def test_clearing_a_model_falls_back_to_the_default(store):
    cp.set_provider_model("openai", "gpt-4.1-mini")
    assert cp.set_provider_model("openai", "") is True
    assert cp.resolve_model("openai") == "gpt-4o-mini"


# ── describe payload ──────────────────────────────────────────────────────


def test_describe_reports_where_each_model_came_from(store, monkeypatch):
    cp.set_provider_model("openai", "gpt-4.1-mini")
    monkeypatch.setenv("CUTTLE_LLM_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    rows = {p["id"]: p for p in cp.describe()["providers"]}
    assert rows["openai"]["model_from"] == "settings"
    assert rows["openai"]["effective_model"] == "gpt-4.1-mini"
    assert rows["anthropic"]["model_from"] == "env"
    assert rows["local"]["model_from"] == "default"


def test_describe_marks_the_pinned_provider(store):
    cp.set_preferred_provider("local")
    payload = cp.describe()
    assert payload["pinned_provider"] == "local"
    pinned = [p["id"] for p in payload["providers"] if p["pinned"]]
    assert pinned == ["local"]


def test_describe_survives_a_broken_settings_store(monkeypatch):
    class _Boom:
        def get_setting(self, *_a, **_k):
            raise RuntimeError("settings exploded")

    monkeypatch.setattr(cp, "_settings", lambda: _Boom())
    assert cp.preferred_provider_id() is None
    assert cp.resolve_model("openai") == "gpt-4o-mini"
    assert len(cp.describe()["providers"]) == len(cp.DEFAULT_ORDER)


# ── the writes are actually read (the old controls' failing) ──────────────


def test_llm_complete_reads_the_registry(monkeypatch):
    """The regression that made the dropdowns dead: a Settings write that
    api.llm_complete never consults."""
    from api import llm_complete

    seen = {}

    def _fake_openai(user, *, system, model, max_tokens, temperature,
                     timeout, json_object):
        seen["model"] = model
        return "ok"

    monkeypatch.setattr(llm_complete, "_via_openai", _fake_openai)

    # No settings store in this test: registry falls back to provider default.
    assert llm_complete.complete(user="hi", providers=("openai",)) == "ok"
    assert seen["model"] == "gpt-4o-mini"


def test_llm_complete_order_comes_from_the_registry(monkeypatch, store):
    from api import llm_complete

    tried = []

    def _fake(name):
        def _call(*_a, **_k):
            tried.append(name)
            return None
        return _call

    monkeypatch.setattr(llm_complete, "_via_openai", _fake("openai"))
    monkeypatch.setattr(llm_complete, "_via_anthropic", _fake("anthropic"))
    monkeypatch.setattr(llm_complete, "_via_local", _fake("local"))

    cp.set_preferred_provider("anthropic")
    llm_complete.complete(user="hi")
    assert tried == ["anthropic", "openai", "local"]


def test_llm_complete_local_mode_stays_local_only(monkeypatch):
    """inference_mode=local must not walk the chain and burn a cloud key."""
    from api import llm_complete

    tried = []
    monkeypatch.setattr(
        llm_complete, "_via_local", lambda *a, **k: tried.append("local") or None
    )
    monkeypatch.setattr(
        llm_complete, "_via_openai", lambda *a, **k: tried.append("openai") or None
    )
    llm_complete.complete(user="hi", inference_mode="local")
    assert tried == ["local"]


# ── route ─────────────────────────────────────────────────────────────────


def test_route_rejects_an_unknown_provider():
    from api import settings_routes

    ok, message, _patch = settings_routes.validate_completion_providers_update(
        {"provider": "nope"}
    )
    assert ok is False
    assert "nope" in message


def test_route_normalizes_auto_and_rejects_an_empty_body():
    from api import settings_routes

    ok, _m, patch = settings_routes.validate_completion_providers_update(
        {"provider": "AUTO"}
    )
    assert ok is True
    assert patch == {"provider": ""}

    ok, message, _p = settings_routes.validate_completion_providers_update({})
    assert ok is False
    assert message


def test_route_keeps_models_free_form_and_drops_blanks():
    from api import settings_routes

    ok, _m, patch = settings_routes.validate_completion_providers_update(
        {"models": {"openai": "gpt-5.2", "anthropic": "   ", "bogus": "x"}}
    )
    # An unknown provider id in the map is a client bug, not a silent drop.
    assert ok is False

    ok, _m, patch = settings_routes.validate_completion_providers_update(
        {"models": {"openai": "gpt-5.2", "anthropic": "  "}}
    )
    assert ok is True
    assert patch == {"models": {"openai": "gpt-5.2"}}


def test_route_requires_authentication(monkeypatch):
    from api import web_chat_api as wca

    anon = wca.app.test_client()
    res = anon.get("/api/settings/completion-providers")
    assert res.status_code == 401, res.get_json()
    res = anon.post("/api/settings/completion-providers", json={"provider": "local"})
    assert res.status_code == 401, res.get_json()


def test_route_round_trips_a_pin(store, tmp_path, monkeypatch):
    """A Settings write must come back on read, or the control is a lie."""
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca

    db_path = tmp_path / "completion-providers.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    client = wca.app.test_client()
    client.set_cookie("session_token", db.create_auth_session(owner))

    res = client.post(
        "/api/settings/completion-providers",
        json={"provider": "anthropic", "models": {"anthropic": "claude-haiku-4-5-20251001"}},
    )
    assert res.status_code == 200, res.get_json()
    payload = res.get_json()
    assert payload["pinned_provider"] == "anthropic"
    assert payload["order"][0] == "anthropic"
    assert client.get("/api/settings/completion-providers").get_json()[
        "pinned_provider"
    ] == "anthropic"


def test_route_write_is_owner_only(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca

    db_path = tmp_path / "completion-providers-guest.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    db.create_user("owner@local", "Owner", "local", password="x")

    client = wca.app.test_client()
    assert client.post("/api/auth/guest", json={}).status_code == 200
    assert client.post(
        "/api/settings/completion-providers", json={"provider": "local"}
    ).status_code == 403


def test_route_is_registered_in_the_settings_family_registry():
    from api import settings_routes

    names = {row["name"] for row in settings_routes.SETTING_FAMILIES}
    assert "completion-providers" in names