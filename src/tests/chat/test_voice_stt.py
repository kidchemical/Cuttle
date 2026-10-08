"""Voice server transcription owner + route (experimental ``voice_server_stt``).

The OpenAI client is always stubbed: no paid transcription call.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from flask import Flask

from api import voice_stt
from api.experimental import flags


class _FakeTranscriptions:
    def __init__(self, text="hello there", fail_models=()):
        self.text = text
        self.fail_models = set(fail_models)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] in self.fail_models:
            raise RuntimeError("model unavailable")
        return SimpleNamespace(text=self.text)


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    tr = _FakeTranscriptions()
    monkeypatch.setattr(voice_stt, "_client", lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=tr)))
    return tr


@pytest.fixture()
def client(monkeypatch, fake):
    from api.voice_stt import routes

    monkeypatch.setattr("api.http_authz.require_authenticated", lambda: ({"id": 1}, None))
    app = Flask(__name__)
    app.register_blueprint(routes.voice_stt_bp)
    return app.test_client()


def _post(client, data=b"OggS-audio", **form):
    body = {"audio": (io.BytesIO(data), "phrase.webm", "audio/webm"), **form}
    return client.post("/api/voice-stt/transcribe", data=body, content_type="multipart/form-data")


def test_flag_registered_default_off():
    spec = flags.get_flag("voice_server_stt")
    assert spec is not None and spec.default is False


def test_clean_transcript_drops_short_silence_hallucinations():
    assert voice_stt.clean_transcript("  Thank you.  ", duration_ms=900) == ""
    assert voice_stt.clean_transcript("Thank you.", duration_ms=4000) == "Thank you."
    assert voice_stt.clean_transcript(" . ") == ""
    assert voice_stt.clean_transcript("fix  the\nmic") == "fix the mic"


def test_transcribe_passes_mime_prompt_language(fake):
    text = voice_stt.transcribe(b"x", mime="audio/mp4", prompt="earlier words", language="en-US")
    assert text == "hello there"
    call = fake.calls[0]
    assert call["model"] == voice_stt.DEFAULT_MODEL
    assert call["file"][0] == "phrase.mp4" and call["file"][2] == "audio/mp4"
    assert call["prompt"] == "earlier words" and call["language"] == "en"


def test_transcribe_falls_back_then_fails_closed(fake):
    fake.fail_models = {voice_stt.DEFAULT_MODEL}
    assert voice_stt.transcribe(b"x") == "hello there"
    assert [c["model"] for c in fake.calls] == [voice_stt.DEFAULT_MODEL, voice_stt.FALLBACK_MODEL]
    fake.fail_models = {voice_stt.DEFAULT_MODEL, voice_stt.FALLBACK_MODEL}
    with pytest.raises(RuntimeError):
        voice_stt.transcribe(b"x")
    with pytest.raises(ValueError):
        voice_stt.transcribe(b"")


def test_route_disabled_while_flag_off(client):
    r = _post(client)
    assert r.status_code == 200 and r.json["disabled"] is True


def test_route_transcribes_upload(client, fake):
    flags.set_enabled("voice_server_stt", True)
    r = _post(client, duration_ms="2400", prompt="context", language="en-US")
    assert r.status_code == 200 and r.json == {"success": True, "text": "hello there"}
    assert fake.calls[0]["file"][1] == b"OggS-audio"


def test_route_rejects_missing_audio(client):
    flags.set_enabled("voice_server_stt", True)
    r = client.post("/api/voice-stt/transcribe", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_route_requires_auth(monkeypatch, fake):
    from api.voice_stt import routes

    monkeypatch.setattr("api.http_authz.require_authenticated", lambda: (None, ({"error": "auth"}, 401)))
    app = Flask(__name__)
    app.register_blueprint(routes.voice_stt_bp)
    assert _post(app.test_client()).status_code == 401
