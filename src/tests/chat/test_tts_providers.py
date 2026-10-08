"""TTS provider registry + voice clip cache (no live provider calls)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api import tts_providers as providers  # noqa: E402
from api import voice_cache  # noqa: E402


def test_registry_lists_three_configured_shaped_providers(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    rows = providers.list_providers()
    assert [r["id"] for r in rows] == ["openai", "elevenlabs", "google"]
    for row in rows:
        assert row["label"] and row["models"] and row["voices"]
        assert row["default_model"]
        assert row["configured"] is False
    assert providers.get_provider("ElevenLabs").id == "elevenlabs"
    assert providers.get_provider("nope") is None


def test_credential_presence_lights_up_configured(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x" * 32)
    assert providers.get_provider("elevenlabs").configured() is True
    assert providers.get_provider("openai").configured() is False


def test_voice_and_model_normalization():
    assert providers.normalize_voice("openai", "coral") == "coral"
    assert providers.normalize_voice("openai", "") == "coral"
    assert providers.normalize_voice("elevenlabs", "custom-id") == "custom-id"
    assert providers.normalize_voice("nope", "x") == ""
    assert providers.normalize_model("google", "") == "gemini-2.5-flash-preview-tts"
    assert providers.normalize_model("openai", "my-future-model") == "my-future-model"


def test_speed_and_stability_clamp_per_provider():
    assert providers.clamp_speed("openai", 9) == 4.0
    assert providers.clamp_speed("elevenlabs", 9) == 1.2
    assert providers.clamp_speed("elevenlabs", 0.1) == 0.7
    assert providers.clamp_speed("google", 2) is None
    assert providers.clamp_stability("elevenlabs", 9) == 1.0
    assert providers.clamp_stability("openai", 0.9) is None


def test_openai_synthesize_posts_model_voice_speed():
    fake_resp = MagicMock()
    fake_resp.content = b"MP3BYTES"
    fake_audio = MagicMock()
    fake_audio.audio.speech.create.return_value = fake_resp
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-x"}), patch(
        "openai.OpenAI", return_value=fake_audio
    ):
        audio, mime, extra = providers.synthesize(
            "openai", "Hello", voice="coral", model="tts-1", speed=1.25
        )
    assert audio == b"MP3BYTES"
    assert mime == "audio/mpeg"
    assert extra == {}
    _, kwargs = fake_audio.audio.speech.create.call_args
    assert kwargs["model"] == "tts-1"
    assert kwargs["voice"] == "coral"
    assert kwargs["speed"] == 1.25


def test_openai_synthesize_needs_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        providers.synthesize("openai", "Hello")


def test_elevenlabs_synthesize_posts_voice_settings():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"ELMP3"
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "x" * 32}), patch(
        "requests.post", return_value=fake_resp
    ) as mock_post:
        audio, mime, _ = providers.synthesize(
            "elevenlabs", "Hello", voice="abc123",
            model="eleven_flash_v2_5", speed=1.1, stability=0.3,
        )
    assert audio == b"ELMP3"
    assert mime == "audio/mpeg"
    url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/abc123"
    assert kwargs["headers"]["xi-api-key"] == "x" * 32
    assert kwargs["json"]["model_id"] == "eleven_flash_v2_5"
    assert kwargs["json"]["voice_settings"] == {"speed": 1.1, "stability": 0.3}


def test_elevenlabs_auth_failure_is_honest():
    fake_resp = MagicMock()
    fake_resp.status_code = 401
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "bad"}), patch(
        "requests.post", return_value=fake_resp
    ), patch("requests.get", return_value=fake_resp):
        with pytest.raises(RuntimeError, match="401"):
            providers.synthesize("elevenlabs", "Hello", voice="abc")
        with pytest.raises(RuntimeError, match="401"):
            providers.fetch_provider_voices("elevenlabs")


def test_elevenlabs_fetch_voices_maps_library():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"voices": [
        {"voice_id": "id-1", "name": "Mine"},
        {"voice_id": "", "name": "Broken"},
    ]}
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "x" * 32}), patch(
        "requests.get", return_value=fake_resp
    ):
        voices = providers.fetch_provider_voices("elevenlabs")
    assert voices == [{"id": "id-1", "label": "Mine"}]


def test_google_synthesize_decodes_audio_and_usage():
    import base64

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "candidates": [{"content": {"parts": [
            {"inlineData": {"data": base64.b64encode(b"WAVBYTES").decode(),
                            "mimeType": "audio/wav"}}]}}],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 480},
    }
    with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza" + "x" * 35}), patch(
        "requests.post", return_value=fake_resp
    ) as mock_post:
        audio, mime, extra = providers.synthesize(
            "google", "Hello", voice="Kore",
            model="gemini-2.5-flash-preview-tts",
        )
    assert audio == b"WAVBYTES"
    assert mime == "audio/wav"
    assert extra == {"input_tokens": 12, "output_tokens": 480}
    body = mock_post.call_args[1]["json"]
    assert body["generationConfig"]["speechConfig"]["voiceConfig"] == {
        "prebuiltVoiceConfig": {"voiceName": "Kore"}
    }


def test_google_has_no_list_endpoint():
    assert providers.fetch_provider_voices("google") is None


def test_synthesize_rejects_unknown_provider_and_empty_text():
    with pytest.raises(ValueError, match="Unknown TTS provider"):
        providers.synthesize("nope", "Hello")
    with pytest.raises(ValueError, match="Nothing to speak"):
        providers.synthesize("openai", "   ")


def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    return tmp_path / "home" / "cache" / "voice_tts"


def test_cache_key_stable_and_setting_sensitive():
    a = voice_cache.cache_key("openai", "tts-1", "coral", 1.0, None, "Hi")
    assert a == voice_cache.cache_key("openai", "tts-1", "coral", 1.0, None, "Hi")
    assert a != voice_cache.cache_key("openai", "tts-1", "coral", 1.25, None, "Hi")
    assert a != voice_cache.cache_key("elevenlabs", "tts-1", "coral", 1.0, None, "Hi")
    assert a != voice_cache.cache_key("openai", "tts-1", "coral", 1.0, None, "Hi!")


def test_cache_put_get_round_trip(monkeypatch, tmp_path):
    _isolated_cache(monkeypatch, tmp_path)
    key = voice_cache.cache_key("openai", "tts-1", "coral", 1.0, None, "Hello")
    assert voice_cache.get(key) is None
    voice_cache.put(key, b"MP3", "audio/mpeg", {"provider": "openai"})
    hit = voice_cache.get(key)
    assert hit is not None
    audio, mime, meta = hit
    assert audio == b"MP3"
    assert mime == "audio/mpeg"
    assert meta["provider"] == "openai"


def test_cache_expires_after_ttl(monkeypatch, tmp_path):
    import os

    d = _isolated_cache(monkeypatch, tmp_path)
    key = voice_cache.cache_key("openai", "tts-1", "coral", 1.0, None, "Old")
    voice_cache.put(key, b"MP3", "audio/mpeg", {})
    old = time.time() - voice_cache.TTL_SECONDS - 10
    for p in d.glob(f"{key}.*"):
        os.utime(p, (old, old))
    assert voice_cache.get(key) is None


def test_cache_evicts_oldest_past_cap(monkeypatch, tmp_path):
    d = _isolated_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(voice_cache, "MAX_BYTES", 10)
    voice_cache.put("k1", b"123456", "audio/mpeg", {})
    voice_cache.put("k2", b"123456", "audio/mpeg", {})
    assert voice_cache.get("k1") is None
    assert voice_cache.get("k2") is not None
    assert (d / "k1.json").exists() is False
