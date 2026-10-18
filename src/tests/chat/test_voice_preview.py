"""Settings voice previews: real Realtime sample (faked provider) and Live gating."""
import base64
import io
import wave
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from flask import Flask

from api.experimental import flags
from api.voice_live import settings as live_settings
from api.voice_live.routes import voice_live_bp
from api.voice_realtime import preview
from api.voice_realtime.routes import voice_realtime_bp


class FakeConnection:
    def __init__(self, events):
        self.events, self.sent, self.closed = list(events), [], False

    def send(self, event):
        self.sent.append(event)

    def close(self):
        self.closed = True

    def __iter__(self):
        yield from self.events


@pytest.fixture
def isolated(tmp_path, monkeypatch, _isolated_application_settings):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    yield


def _fake_provider(monkeypatch, events):
    opened = []

    @contextmanager
    def fake_client():
        def connect(**kwargs):
            connection = FakeConnection(events)
            opened.append((kwargs, connection))

            @contextmanager
            def manager():
                yield connection
            return manager()
        yield SimpleNamespace(realtime=SimpleNamespace(connect=connect))

    monkeypatch.setattr(preview, "_realtime_client", fake_client)
    return opened


AUDIO = [
    {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00" * 240).decode()},
    {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x02\x00" * 240).decode()},
    {"type": "response.done", "response": {"id": "resp_1", "usage": {"output_tokens": 40,
        "output_token_details": {"audio_tokens": 40}}}},
]


def test_realtime_preview_speaks_with_the_selected_voice_and_caches(isolated, monkeypatch):
    opened = _fake_provider(monkeypatch, AUDIO)
    recorded = []
    monkeypatch.setattr(preview.voice_usage, "record_realtime_usage",
                        lambda model, responses, **kw: recorded.append((model, responses, kw)) or {"cost": 0.0026})

    audio, mime, meta = preview.synthesize("gpt-realtime-2.1-mini", "cedar")
    assert mime == "audio/wav" and meta == {"cached": False, "cost": 0.0026}
    with wave.open(io.BytesIO(audio)) as clip:
        assert (clip.getnchannels(), clip.getsampwidth(), clip.getframerate(), clip.getnframes()) == (1, 2, 24000, 480)
    kwargs, connection = opened[0]
    assert kwargs == {"model": "gpt-realtime-2.1-mini"}
    assert connection.sent[0]["session"]["audio"]["output"]["voice"] == "cedar"
    assert connection.sent[1]["item"]["content"][0]["text"] == preview.PREVIEW_TEXT
    assert recorded[0][2]["usage_key"] == "realtime-preview:resp_1"

    again, _, meta = preview.synthesize("gpt-realtime-2.1-mini", "cedar")
    assert again == audio and meta["cached"] is True
    assert len(opened) == 1 and len(recorded) == 1


def test_realtime_preview_reports_provider_errors_and_rejects_unlisted_choices(isolated, monkeypatch):
    _fake_provider(monkeypatch, [{"type": "error", "error": {"message": "model not available"}}])
    with pytest.raises(RuntimeError, match="model not available"):
        preview.synthesize("gpt-realtime-2.1", "marin")
    with pytest.raises(ValueError):
        preview.synthesize("gpt-realtime-2.1", "nova")


def _app(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(voice_realtime_bp, url_prefix="/api/voice-stt/realtime")
    app.register_blueprint(voice_live_bp, url_prefix="/api/voice-stt/live")
    monkeypatch.setattr("api.http_authz.require_owner", lambda: ({"id": 7}, None))
    return app.test_client()


def test_realtime_preview_route_is_gated_by_its_flag(isolated, monkeypatch):
    client = _app(monkeypatch)
    _fake_provider(monkeypatch, AUDIO)
    monkeypatch.setattr(preview.voice_usage, "record_realtime_usage", lambda *a, **k: {"cost": 0.001})

    flags.set_enabled("voice_realtime", False)
    off = client.post("/api/voice-stt/realtime/preview", json={"voice": "marin"})
    assert off.status_code == 403 and off.json["disabled"]

    flags.set_enabled("voice_realtime", True)
    ok = client.post("/api/voice-stt/realtime/preview", json={"voice": "marin"})
    assert ok.status_code == 200 and ok.json["content_type"] == "audio/wav" and ok.json["cost"] == 0.001
    assert base64.b64decode(ok.json["audio_base64"]).startswith(b"RIFF")


def test_live_preview_only_offers_voices_tts_can_speak(isolated, monkeypatch):
    client = _app(monkeypatch)
    flags.set_enabled("voice_live", True)
    voices = {v["id"]: v["preview"] for v in live_settings.catalog()["voices"]}
    assert voices["marin"] is True and voices["juni"] is False

    calls = []
    monkeypatch.setattr("api.tts_providers.synthesize", lambda *a, **k: calls.append(k) or (b"MP3", "audio/mpeg", {}))
    refused = client.post("/api/voice-stt/live/preview", json={"voice": "juni"})
    assert refused.status_code == 400 and calls == []
    ok = client.post("/api/voice-stt/live/preview", json={"voice": "marin"})
    assert ok.status_code == 200 and ok.json["cached"] is False and len(calls) == 1
    cached = client.post("/api/voice-stt/live/preview", json={"voice": "marin"})
    assert cached.json["cached"] is True and len(calls) == 1
