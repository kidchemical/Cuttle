"""Manual chat rename + title suggestion endpoints."""

from __future__ import annotations

from pathlib import Path

from api.auth_db import AuthDatabase


def _seed_chat(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    other = db.create_user("other@local", "Other", "local", password="x")
    sid = db.create_chat_session(owner, "Chat Session 1")
    db.add_message(sid, "user", "/cursor rename chats like pending changes")
    db.add_message(sid, "assistant", "I'll add a suggest + pencil modal.")
    foreign = db.create_chat_session(other, "Foreign chat")
    db.add_message(foreign, "user", "not yours")
    token = db.create_auth_session(owner)
    return db, owner, sid, foreign, token


def test_rename_chat_session_locks_auto_name(tmp_path: Path):
    db, owner, sid, _foreign, _token = _seed_chat(tmp_path)
    assert db.rename_chat_session(sid, owner, "✨ Rename chat titles") is True
    info = db.get_session_naming_info(sid)
    assert info["session_name"] == "✨ Rename chat titles"
    assert int(info["name_auto"] or 0) == 0
    # Auto-titler must not overwrite a manual rename.
    assert db.set_session_name(sid, "should not stick", auto=True) is False
    assert db.get_session_naming_info(sid)["session_name"] == "✨ Rename chat titles"


def test_rename_chat_session_rejects_other_user(tmp_path: Path):
    db, owner, sid, foreign, _token = _seed_chat(tmp_path)
    assert db.rename_chat_session(foreign, owner, "Nope") is False
    assert db.get_session_naming_info(foreign)["session_name"] == "Foreign chat"


def test_suggest_session_title_uses_llm_without_saving(tmp_path: Path, monkeypatch):
    from api import chat_titler

    db, _owner, sid, _foreign, _token = _seed_chat(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)

    def fake_generate(messages, current_title="", avoid_titles=None, temperature=None):
        assert messages
        return "✨ Rename chat titles"

    monkeypatch.setattr(chat_titler, "_generate_title", fake_generate)
    before = db.get_session_naming_info(sid)["session_name"]
    out = chat_titler.suggest_session_title(sid)
    assert out["source"] == "llm"
    assert out["title"] == "✨ Rename chat titles"
    assert "/cursor" not in out["title"]
    assert out["slash_prefixes"].startswith("/cursor")
    assert out["composed"].startswith("/cursor")
    assert "Rename chat titles" in out["composed"]
    assert db.get_session_naming_info(sid)["session_name"] == before


def test_suggest_session_title_avoids_previous(tmp_path: Path, monkeypatch):
    from api import chat_titler

    db, _owner, sid, _foreign, _token = _seed_chat(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    seen = []

    def fake_generate(messages, current_title="", avoid_titles=None, temperature=None):
        seen.append(list(avoid_titles or []))
        return "🧪 Fresh title"

    monkeypatch.setattr(chat_titler, "_generate_title", fake_generate)
    out = chat_titler.suggest_session_title(
        sid,
        avoid_titles=["💬 general"],
    )
    assert out["title"] == "🧪 Fresh title"
    assert "💬 general" in seen[0]


def test_build_prompt_omits_stale_current_title():
    from api import chat_titler

    messages = [
        {"role": "user", "content": "/muse hello"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "/muse why is double-checking slow? RCA it"},
    ]
    prompt = chat_titler._build_prompt(messages, current_title="hello")
    assert "Current title" not in prompt
    assert "double-checking slow" in prompt


def test_fallback_skips_greeting_opener():
    from api import chat_titler

    messages = [
        {"role": "user", "content": "/muse hello"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "/muse Why is muse code slow to double check?"},
    ]
    assert "double check" in chat_titler.fallback_from_messages(messages).lower()
    assert chat_titler.fallback_from_messages(messages[:1]) == "/muse hello"


def test_fallback_skips_pasted_terminal_output():
    from api import chat_titler

    paste = "/muse " + "…/cuttle-pet  ❯ muse export --session 01a0fdf3 " + ("x" * 600)
    messages = [
        {"role": "user", "content": "/muse hello"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "/muse Why is double-checking slow? RCA it"},
        {"role": "user", "content": paste},
    ]
    assert "double-checking" in chat_titler.fallback_from_messages(messages).lower()
    # Paste-only chats still get a (truncated) title rather than nothing.
    assert chat_titler.fallback_from_messages(messages[:1] + messages[-1:])


def test_suggest_falls_back_to_recent_topic(tmp_path: Path, monkeypatch):
    from api import chat_titler
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "Chat Session 1")
    db.add_message(sid, "user", "/muse hello")
    db.add_message(sid, "assistant", "Hello!")
    db.add_message(sid, "user", "/muse Why is double-checking slow? RCA it")
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(chat_titler, "_generate_title", lambda *_a, **_k: None)
    out = chat_titler.suggest_session_title(sid)
    assert out["source"] == "fallback"
    assert "double-checking" in out["title"].lower()


def test_patch_and_suggest_title_api(tmp_path: Path, monkeypatch):
    from api import web_chat_api as wca
    from api import chat_titler

    db, _owner, sid, foreign, token = _seed_chat(tmp_path)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(
        chat_titler,
        "_generate_title",
        lambda *_a, **_k: "🎨 History rename modal",
    )

    client = wca.app.test_client()
    client.set_cookie("session_token", token)

    suggest = client.post(
        f"/api/auth/sessions/{sid}/suggest-title",
        json={},
    )
    assert suggest.status_code == 200
    body = suggest.get_json()
    assert body["success"] is True
    assert body["source"] == "llm"
    assert "History rename modal" in body["title"]
    assert "/cursor" not in body["title"]
    assert body["composed"].startswith("/cursor")
    # Suggestion must not persist.
    assert db.get_session_naming_info(sid)["session_name"] == "Chat Session 1"

    patch = client.patch(
        f"/api/auth/sessions/{sid}",
        json={"session_name": "  My custom title  "},
    )
    assert patch.status_code == 200
    patched = patch.get_json()
    assert patched["success"] is True
    assert patched["title"] == "My custom title"
    assert patched["session_name"].startswith("/cursor")
    assert "My custom title" in patched["session_name"]
    assert patched["name_auto"] == 0
    info = db.get_session_naming_info(sid)
    assert "My custom title" in info["session_name"]
    assert int(info["name_auto"] or 0) == 0

    # Slash chips in the typed name are stripped; transcript chips are re-applied.
    patch2 = client.patch(
        f"/api/auth/sessions/{sid}",
        json={"session_name": "/muse Should not keep muse badge"},
    )
    assert patch2.status_code == 200
    body2 = patch2.get_json()
    assert body2["title"] == "Should not keep muse badge"
    assert body2["session_name"].startswith("/cursor")
    assert "/muse" not in body2["session_name"]

    denied = client.patch(
        f"/api/auth/sessions/{foreign}",
        json={"session_name": "hacked"},
    )
    assert denied.status_code == 404
