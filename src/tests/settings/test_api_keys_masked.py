"""Credential endpoints never expose full secrets (Issue 5)."""

from __future__ import annotations


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
    # Isolate the .env file the endpoints read/write.
    monkeypatch.setattr(wca, "actual_project_root", tmp_path)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client


def test_load_returns_hints_never_plaintext(tmp_path, monkeypatch):
    from api import web_chat_api as wca

    client = _owner_client(tmp_path, monkeypatch)
    secret = "sk-test-openai-secret-value-1234"
    (tmp_path / "src" / ".env").write_text(
        f"# comment line\n\nexport OTHER=keep\nOPENAI_API_KEY={secret}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = client.get("/api/load-api-keys")
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["configured"]["openai"] is True
    hint = body["api_keys"]["openai"]
    assert hint and secret not in hint
    assert hint.startswith(secret[:4]) and hint.endswith(secret[-4:])
    raw = res.get_data(as_text=True)
    assert secret not in raw


def test_save_preserves_unrelated_lines_and_applies_env(tmp_path, monkeypatch):
    client = _owner_client(tmp_path, monkeypatch)
    (tmp_path / "src" / ".env").write_text(
        "# keep me\n\nexport OTHER=keep\nOPENAI_API_KEY=old\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = client.post(
        "/api/save-api-key",
        json={"api_type": "openai", "api_key": "sk-new-value-abcdef123456"},
    )
    assert res.status_code == 200
    text = (tmp_path / "src" / ".env").read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "export OTHER=keep" in text
    assert "OPENAI_API_KEY=sk-new-value-abcdef123456" in text
    assert "old" not in text
    import os

    assert os.environ.get("OPENAI_API_KEY") == "sk-new-value-abcdef123456"

    # Saving one credential must not disturb the other.
    res = client.post(
        "/api/save-api-key",
        json={"api_type": "anthropic", "api_key": "sk-ant-new-value-123456"},
    )
    assert res.status_code == 200
    text = (tmp_path / "src" / ".env").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-new-value-abcdef123456" in text


def test_save_rejects_unknown_type_and_masked_hints(tmp_path, monkeypatch):
    client = _owner_client(tmp_path, monkeypatch)
    (tmp_path / "src" / ".env").write_text("", encoding="utf-8")

    res = client.post(
        "/api/save-api-key", json={"api_type": "bogus", "api_key": "x" * 20}
    )
    assert res.status_code == 400
    assert (tmp_path / "src" / ".env").read_text(encoding="utf-8") == ""

    res = client.post(
        "/api/save-api-key",
        json={"api_type": "openai", "api_key": "sk-t••••••••1234"},
    )
    assert res.status_code == 400


def test_discord_legacy_name_visible_and_converged(tmp_path, monkeypatch):
    from api import web_chat_api as wca

    client = _owner_client(tmp_path, monkeypatch)
    legacy = "x" * 60
    (tmp_path / "src" / ".env").write_text(
        f"DISCORD_TOKEN={legacy}\n", encoding="utf-8"
    )
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    res = client.get("/api/load-api-keys")
    body = res.get_json()
    assert body["configured"]["discord"] is True
    assert legacy not in res.get_data(as_text=True)

    new_tok = "y" * 60
    res = client.post(
        "/api/save-api-key", json={"api_type": "discord", "api_key": new_tok}
    )
    assert res.status_code == 200
    text = (tmp_path / "src" / ".env").read_text(encoding="utf-8")
    assert "DISCORD_BOT_TOKEN=" in text
    assert "DISCORD_TOKEN=" not in text


def test_load_requires_owner(tmp_path, monkeypatch):
    from api import web_chat_api as wca

    _owner_client(tmp_path, monkeypatch)
    anon = wca.app.test_client()
    res = anon.get("/api/load-api-keys")
    assert res.status_code == 401


def test_test_endpoint_uses_saved_key_when_input_empty(tmp_path, monkeypatch):
    from flask import jsonify

    from api import web_chat_api as wca

    client = _owner_client(tmp_path, monkeypatch)
    secret = "sk-saved-openai-secret-5678"
    (tmp_path / "src" / ".env").write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    seen = {}

    def fake_test(key):
        seen["key"] = key
        return jsonify({"success": True, "message": "ok"})

    monkeypatch.setattr(wca, "test_openai_key", fake_test)
    res = client.post("/api/test-api-key", json={"api_type": "openai", "api_key": ""})
    assert res.status_code == 200
    assert seen["key"] == secret

    # Nothing saved and nothing typed → a clear message, no provider call.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post("/api/test-api-key", json={"api_type": "anthropic"})
    assert res.status_code == 400
    assert "No key saved" in res.get_json()["message"]
