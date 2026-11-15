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
    def transport(provider, request, **_kw):
        assert provider == "openai"
        kwargs = dict(model=request.model, file=(request.filename, request.audio, request.mime))
        if request.prompt:
            kwargs['prompt'] = request.prompt
        if request.language:
            kwargs['language'] = request.language
        return tr.create(**kwargs).text
    monkeypatch.setattr("api.model_providers.service.transcribe", transport)
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


def test_flag_registered_default_on_and_compatible_with_conversation_engines():
    # Default on: the only voice input that works in the Electron app.
    spec = flags.get_flag("voice_server_stt")
    assert spec is not None and spec.default is True
    assert flags.incompatible_flags("voice_server_stt") == []


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
    flags.set_enabled("voice_server_stt", False)
    r = _post(client)
    assert r.status_code == 200 and r.json["disabled"] is True


def test_status_reports_whether_openai_key_is_configured(client, monkeypatch):
    # Voice mode falls back to browser speech when no key is configured.
    monkeypatch.setattr(voice_stt, "configured", lambda: False)
    r = client.get("/api/voice-stt/status")
    assert r.status_code == 200
    body = dict(r.json)
    clip = body.pop("clip")
    assert body == {"success": True, "enabled": True, "configured": False}
    # The composer mic reads its provider, live-text engine and length cap here.
    assert clip["max_clip_ms"] == 600000 and clip["live_text"] is True and clip["enabled"] is True
    monkeypatch.setattr(voice_stt, "configured", lambda: True)
    assert client.get("/api/voice-stt/status").json["configured"] is True


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


# ---- Speech-to-text provider choice (Settings → Voice → Speech to text) ----

def _prefs(monkeypatch, **over):
    from api.voice_stt import settings

    prefs = settings.normalize(over)
    monkeypatch.setattr(settings, "load", lambda: prefs)
    return settings


def test_settings_defaults_and_validation():
    from api.voice_stt import settings

    assert settings.normalize(None) == {"provider": "openai", "models": {}, "local_base_url": "",
                                        "live_text": True, "max_clip_minutes": 10}
    assert {p.id for p in settings.providers()} >= {"openai", "local", "elevenlabs"}
    for bad in ({"provider": "nope"}, {"max_clip_minutes": 0}, {"max_clip_minutes": 31},
                {"max_clip_minutes": True}, {"local_base_url": "ftp://x"}, {"live_text": "yes"},
                {"models": {"openai": 3}}):
        with pytest.raises(ValueError):
            settings.normalize(bad)
    kept = settings.normalize({"models": {"openai": " whisper-1 ", "unknown": "x"}, "local_base_url": "http://box:8000/v1/"})
    assert kept["models"] == {"openai": "whisper-1"} and kept["local_base_url"] == "http://box:8000/v1"


def test_transcribe_uses_the_chosen_provider_model_and_local_address(monkeypatch):
    _prefs(monkeypatch, provider="local", models={"local": "base.en"}, local_base_url="http://box:8000/v1")
    seen = []

    def transport(provider, request, **_kw):
        seen.append((provider, request.model, request.base_url))
        return "from the box"

    monkeypatch.setattr("api.model_providers.service.transcribe", transport)
    assert voice_stt.transcribe(b"RIFF", mime="audio/wav") == "from the box"
    assert seen == [("local", "base.en", "http://box:8000/v1")]


def test_default_model_chain_comes_from_the_provider_manifest(monkeypatch):
    settings = _prefs(monkeypatch, provider="elevenlabs")
    assert settings.resolve() == ("elevenlabs", ["scribe_v2"], "")
    settings = _prefs(monkeypatch)
    assert settings.resolve() == ("openai", [voice_stt.DEFAULT_MODEL, voice_stt.FALLBACK_MODEL], "")


def test_browser_recognizer_never_transcribes_on_the_server(monkeypatch, fake):
    settings = _prefs(monkeypatch, provider="browser")
    assert settings.configured() is False
    assert settings.clip_status()["engine"] == "browser"
    with pytest.raises(RuntimeError, match="browser"):
        voice_stt.transcribe(b"OggS")
    assert fake.calls == []


def test_live_text_off_or_no_credentials_means_no_live_engine(monkeypatch):
    settings = _prefs(monkeypatch, provider="browser", live_text=False)
    assert settings.clip_status()["engine"] == "none"
    settings = _prefs(monkeypatch, provider="elevenlabs", max_clip_minutes=3)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    status = settings.clip_status()
    assert status["engine"] == "none" and status["configured"] is False and status["max_clip_ms"] == 180000


def test_clip_phrases_pass_under_the_composer_flag(client, fake):
    flags.set_enabled("voice_server_stt", False)
    flags.set_enabled("composer_voice_clip", True)
    r = _post(client, purpose="clip")
    assert r.status_code == 200 and r.json == {"success": True, "text": "hello there"}
    flags.set_enabled("composer_voice_clip", False)
    assert _post(client, purpose="clip").json["disabled"] is True


def test_elevenlabs_and_local_adapters_send_the_documented_request(monkeypatch):
    from api.model_providers.catalog import get_adapter, get_provider
    from api.model_providers.types import TranscriptionRequest

    sent = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "scribed"}

    def post(url, **kw):
        sent.update(url=url, **kw)
        return _Resp()

    monkeypatch.setenv("ELEVENLABS_API_KEY", "k" * 24)
    monkeypatch.setattr("api.model_providers.elevenlabs.adapter.requests.post", post)
    request = TranscriptionRequest(b"OggS", "scribe_v2", filename="phrase.webm", language="en")
    assert get_adapter("elevenlabs").transcribe(get_provider("elevenlabs"), request) == "scribed"
    assert sent["url"].endswith("/v1/speech-to-text")
    assert sent["data"] == {"model_id": "scribe_v2", "language_code": "en"}
    assert sent["files"]["file"][0] == "phrase.webm" and sent["headers"]["xi-api-key"] == "k" * 24

    made = {}

    class _SDK:
        def __init__(self, **kw):
            made.update(kw)
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(
                create=lambda **k: made.update(call=k) or SimpleNamespace(text="local words")))

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("openai.OpenAI", _SDK)
    request = TranscriptionRequest(b"RIFF", "whisper-1", filename="phrase.wav", mime="audio/wav",
                                   base_url="http://box:8000/v1")
    assert get_adapter("local").transcribe(get_provider("local"), request) == "local words"
    assert made["base_url"] == "http://box:8000/v1" and made["call"]["model"] == "whisper-1"
