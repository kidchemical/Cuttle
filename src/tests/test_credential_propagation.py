"""Credential changes reach live API clients without a restart (Issue 5)."""

from __future__ import annotations

import os
import types


def _owner_client(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca

    db_path = tmp_path / "auth.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    monkeypatch.setattr(wca, "actual_project_root", tmp_path)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client


def _stub_openai(monkeypatch, captured):
    import openai

    instances = []

    class FakeCompletions:
        def create(self, **kwargs):
            msg = types.SimpleNamespace(content="hello")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class FakeClient:
        def __init__(self, api_key=None, **kwargs):
            captured["key"] = api_key
            instances.append(self)
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return instances


def _stub_anthropic(monkeypatch, captured):
    import anthropic

    instances = []

    class FakeMessages:
        def create(self, **kwargs):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="hello")]
            )

    class FakeClient:
        def __init__(self, api_key=None, **kwargs):
            captured["key"] = api_key
            instances.append(self)
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return instances


def test_openai_key_propagates_to_new_calls(tmp_path, monkeypatch):
    from api.llm_complete import _via_openai

    captured: dict = {}
    instances = _stub_openai(monkeypatch, captured)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old-value-00000000000001")
    monkeypatch.delenv("API_KEY", raising=False)

    assert _via_openai(user="hi", system="", model="gpt-4o-mini",
                       max_tokens=10, temperature=0, timeout=5, json_object=False) == "hello"
    assert captured["key"] == "sk-old-value-00000000000001"
    first_client = instances[-1]

    # A credential change (as save-api-key applies it) reaches the NEXT call
    # via a freshly constructed client — no restart, no stale reuse.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-new-value-00000000000002")
    assert _via_openai(user="hi", system="", model="gpt-4o-mini",
                       max_tokens=10, temperature=0, timeout=5, json_object=False) == "hello"
    assert captured["key"] == "sk-new-value-00000000000002"
    assert instances[-1] is not first_client


def test_anthropic_key_propagates_to_new_calls(tmp_path, monkeypatch):
    from api.llm_complete import _via_anthropic

    captured: dict = {}
    _stub_anthropic(monkeypatch, captured)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-old-00000000000001")
    assert _via_anthropic(user="hi", system="", model="m",
                          max_tokens=10, temperature=0) == "hello"
    assert captured["key"] == "sk-ant-old-00000000000001"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-new-00000000000002")
    assert _via_anthropic(user="hi", system="", model="m",
                          max_tokens=10, temperature=0) == "hello"
    assert captured["key"] == "sk-ant-new-00000000000002"


def test_save_endpoint_change_reaches_llm_client(tmp_path, monkeypatch):
    from api.llm_complete import _via_openai

    captured: dict = {}
    _stub_openai(monkeypatch, captured)
    client = _owner_client(tmp_path, monkeypatch)
    (tmp_path / "src" / ".env").write_text("", encoding="utf-8")
    for var in ("OPENAI_API_KEY", "API_KEY"):
        monkeypatch.delenv(var, raising=False)

    res = client.post("/api/save-api-key", json={
        "api_type": "openai", "api_key": "sk-live-value-00000000000003",
    })
    assert res.status_code == 200
    assert os.environ.get("OPENAI_API_KEY") == "sk-live-value-00000000000003"
    assert _via_openai(user="hi", system="", model="gpt-4o-mini",
                       max_tokens=10, temperature=0, timeout=5, json_object=False) == "hello"
    assert captured["key"] == "sk-live-value-00000000000003"


def test_discord_token_reread_per_call(monkeypatch):
    from api.discord_ops import token as token_mod

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "live-token-1")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    assert token_mod.load_discord_bot_token() == "live-token-1"
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "live-token-2")
    assert token_mod.load_discord_bot_token() == "live-token-2"
