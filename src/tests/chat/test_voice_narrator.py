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
    monkeypatch.setattr("api.chat_tts.synthesize_speech", lambda text, **kw: b"MP3:" + text.encode())
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


def test_strip_slash():
    assert voice_narrator.strip_slash("/cursor fix the mic") == "fix the mic"
    assert voice_narrator.strip_slash("fix the mic") == "fix the mic"


def test_ack_prompt_carries_request_and_mode(_no_external):
    line = voice_narrator.ack_line("/cursor make the mic stay on", mode="steer")
    assert line == "Got it, checking the voice module now."
    prompt = _no_external.calls[-1]["user"]
    assert "make the mic stay on" in prompt and "/cursor" not in prompt
    assert "folding this into it" in prompt


def test_ack_promises_to_keep_user_posted(_no_external):
    voice_narrator.ack_line("/cursor make the mic stay on")
    assert "keep them posted" in _no_external.calls[-1]["system"]


@pytest.mark.parametrize("kind", ["ack", "heartbeat", "progress"])
def test_narrator_speaks_as_cuttle_never_names_the_agent(_no_external, kind):
    if kind == "ack":
        voice_narrator.ack_line("/codex run the tests")
    elif kind == "heartbeat":
        voice_narrator.progress_line("/codex run the tests", [], elapsed_sec=30)
    else:
        voice_narrator.progress_line("/codex run the tests", ["tool 1: Run pytest"])
    call = _no_external.calls[-1]
    assert "You are Cuttle" in call["system"] and "first person" in call["system"]
    assert 'never say "the agent"' in call["system"]
    assert "Codex" not in call["user"] and "Agent:" not in call["user"]


def test_progress_skip_says_nothing(_no_external):
    _no_external.reply = "SKIP"
    assert voice_narrator.progress_line("/cursor x", ["Editing chat_voice.js"]) is None


def test_quiet_agent_gets_still_working_heartbeat(_no_external):
    _no_external.reply = "Still on it."
    line = voice_narrator.progress_line("/cursor fix the mic", [], ["On it."], elapsed_sec=40)
    assert line == "Still on it."
    call = _no_external.calls[-1]
    assert "still working" in call["system"]
    assert "about 40 seconds" in call["user"] and "- On it." in call["user"]


def test_progress_prompt_carries_events_and_said(_no_external):
    _no_external.reply = "I'm editing the voice controller."
    line = voice_narrator.progress_line(
        "/cursor x", ["Reading chat_page.js", "Editing chat_voice.js"], ["On it."]
    )
    assert line == "I'm editing the voice controller."
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
