"""Voice narrator owner + route (experimental ``voice_narrator``).

Every test stubs the completion and speech hops: no paid model or TTS call.
"""

from __future__ import annotations

import base64

import pytest
from flask import Flask

from api import voice_narrator
from api.experimental import flags


@pytest.fixture(autouse=True)
def _no_external(monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    calls = []

    def fake_complete(user, system):
        calls.append({"user": user, "system": system})
        return fake_complete.reply

    fake_complete.reply = "Got it, checking the voice module now."
    monkeypatch.setattr(voice_narrator, "_complete", fake_complete)
    monkeypatch.setattr("api.chat_tts.synthesize_speech", lambda text, model, voice: b"MP3:" + text.encode())
    monkeypatch.setattr(
        "api.chat_tts.load_chat_tts_settings",
        lambda: {"enabled": True, "tts_model": "gpt-4o-mini-tts", "voice": "coral"},
    )
    fake_complete.calls = calls
    return fake_complete


@pytest.fixture()
def client(monkeypatch):
    from api.voice_narrator import routes

    monkeypatch.setattr("api.http_authz.require_authenticated", lambda: ({"id": 1}, None))
    app = Flask(__name__)
    app.register_blueprint(routes.voice_narrator_bp)
    return app.test_client()


def test_flag_registered_default_off():
    spec = flags.get_flag("voice_narrator")
    assert spec is not None and spec.default is False
    assert voice_narrator.is_enabled() is False


def test_agent_label_from_sticky_slash():
    assert voice_narrator.agent_label("/cursor fix the mic") == "Cursor"
    assert voice_narrator.agent_label("/codex run tests") == "Codex"
    assert voice_narrator.agent_label("fix the mic") == "the agent"
    assert voice_narrator.strip_slash("/cursor fix the mic") == "fix the mic"


def test_ack_prompt_names_agent_and_mode(_no_external):
    line = voice_narrator.ack_line("/cursor make the mic stay on", mode="steer")
    assert line == "Got it, checking the voice module now."
    prompt = _no_external.calls[-1]["user"]
    assert "make the mic stay on" in prompt and "/cursor" not in prompt
    assert "Cursor's turn that is already running" in prompt


def test_progress_skip_and_empty_events_say_nothing(_no_external):
    assert voice_narrator.progress_line("/cursor x", []) is None
    assert _no_external.calls == [], "no status lines → no model call"
    _no_external.reply = "SKIP"
    assert voice_narrator.progress_line("/cursor x", ["Editing chat_voice.js"]) is None


def test_progress_prompt_carries_events_and_said(_no_external):
    _no_external.reply = "Cursor is editing the voice controller."
    line = voice_narrator.progress_line(
        "/cursor x", ["Reading chat_page.js", "Editing chat_voice.js"], ["On it."]
    )
    assert line == "Cursor is editing the voice controller."
    prompt = _no_external.calls[-1]["user"]
    assert "- Editing chat_voice.js" in prompt and "- On it." in prompt


def test_narrate_validates_request():
    with pytest.raises(ValueError):
        voice_narrator.narrate({"kind": "chat", "message": "hi"})
    with pytest.raises(ValueError):
        voice_narrator.narrate({"kind": "ack", "message": "  "})
    with pytest.raises(ValueError):
        voice_narrator.narrate({"kind": "ack", "message": "hi", "mode": "later"})


def test_route_disabled_while_flag_off(client):
    r = client.post("/api/voice-narrator/narrate", json={"kind": "ack", "message": "hi"})
    assert r.status_code == 200
    assert r.json == {"success": False, "disabled": True, "error": "Voice narrator is disabled"}


def test_route_returns_line_and_audio(client):
    flags.set_enabled("voice_narrator", True)
    r = client.post("/api/voice-narrator/narrate", json={"kind": "ack", "message": "/cursor do it"})
    assert r.status_code == 200 and r.json["success"] is True
    assert r.json["text"] == "Got it, checking the voice module now."
    assert base64.b64decode(r.json["audio_base64"]).startswith(b"MP3:Got it")


def test_route_nothing_to_say_and_bad_request(client, _no_external):
    flags.set_enabled("voice_narrator", True)
    _no_external.reply = "SKIP"
    r = client.post("/api/voice-narrator/narrate", json={"kind": "progress", "message": "x", "events": ["Editing a.py"]})
    assert r.json == {"success": True, "text": None}
    assert client.post("/api/voice-narrator/narrate", json={"kind": "nope", "message": "x"}).status_code == 400


def test_route_requires_auth(monkeypatch):
    from api.voice_narrator import routes

    monkeypatch.setattr(
        "api.http_authz.require_authenticated",
        lambda: (None, ({"error": "auth"}, 401)),
    )
    app = Flask(__name__)
    app.register_blueprint(routes.voice_narrator_bp)
    assert app.test_client().post("/api/voice-narrator/narrate", json={}).status_code == 401
