"""Voice cost accounting: realtime usage pricing + transcription estimates.

No provider or network calls — pricing is pure arithmetic; ledger writes go to a
temporary database.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from api import voice_stt, voice_usage
from api.model_providers import usage as provider_usage
from api.experimental import flags


def test_realtime_cost_splits_audio_text_and_cached():
    responses = [{
        "input_tokens": 120, "output_tokens": 110,
        "input_token_details": {"text_tokens": 100, "audio_tokens": 20,
                                "cached_tokens": 64,
                                "cached_tokens_details": {"text_tokens": 64, "audio_tokens": 0}},
        "output_token_details": {"text_tokens": 10, "audio_tokens": 100},
    }]
    # 36 full text in @4 + 64 cached text @0.40 + 20 audio in @32
    # + 10 text out @24 + 100 audio out @64
    cost = voice_usage.estimate_realtime_cost_usd("gpt-realtime-2.1", responses)
    assert cost == pytest.approx(7449.6 / 1_000_000)


def test_mini_model_is_cheaper_and_unknown_model_prices_none():
    responses = [{"input_token_details": {"audio_tokens": 1000},
                  "output_token_details": {"audio_tokens": 1000}}]
    big = voice_usage.estimate_realtime_cost_usd("gpt-realtime-2.1", responses)
    mini = voice_usage.estimate_realtime_cost_usd("gpt-realtime-2.1-mini", responses)
    assert mini < big
    assert voice_usage.estimate_realtime_cost_usd("gpt-4o-mini", responses) is None


def test_dated_model_suffix_resolves_to_base_rates():
    assert voice_usage.realtime_rates("gpt-realtime-2.1-2026-05-01") == voice_usage.REALTIME_TOKEN_RATES["gpt-realtime-2.1"]


def test_transcription_prefers_per_minute_then_tokens():
    assert voice_usage.estimate_transcription_cost_usd("gpt-4o-mini-transcribe", seconds=60) == pytest.approx(0.003)
    assert voice_usage.estimate_transcription_cost_usd("whisper-1", seconds=30) == pytest.approx(0.003)
    # No duration → token rate.
    tokens = voice_usage.estimate_transcription_cost_usd(
        "gpt-4o-mini-transcribe", audio_tokens=17, output_tokens=9)
    assert tokens == pytest.approx((17 * 1.25 + 9 * 5.0) / 1_000_000)
    assert voice_usage.estimate_transcription_cost_usd(
        "gpt-4o-mini-transcribe", audio_tokens=17, text_tokens=8, output_tokens=9
    ) == pytest.approx((25 * 1.25 + 9 * 5.0) / 1_000_000)


def test_price_payload_shape_matches_chat_footer():
    priced = voice_usage.price_realtime_payload(
        "gpt-realtime-2.1",
        [{"input_token_details": {"audio_tokens": 600}, "output_token_details": {"audio_tokens": 1200}}],
        [{"input_tokens": 10, "output_tokens": 4, "input_token_details": {"audio_tokens": 10}}],
    )
    assert priced["model"] == "gpt-realtime-2.1"
    assert priced["cost_estimated"] is True
    assert priced["prompt_tokens"] == 610 and priced["completion_tokens"] == 1204
    assert priced["realtime_audio_input_tokens"] == 600
    assert priced["realtime_audio_output_tokens"] == 1200
    assert priced["cost"] == pytest.approx(priced["realtime_cost"] + priced["transcription_cost"], abs=1e-5)


def test_record_realtime_usage_writes_one_priced_row(tmp_path):
    db = tmp_path / "calls.db"
    priced = voice_usage.record_realtime_usage(
        "gpt-realtime-2.1",
        [{"input_token_details": {"audio_tokens": 600}, "output_token_details": {"audio_tokens": 1200}}],
        [], session_id=42, db_path=db)
    calls = provider_usage.all_calls(db_path=db)
    assert len(calls) == 1
    row = calls[0]
    assert row["operation"] == "realtime_session" and row["task"] == "realtime"
    assert row["provider"] == "openai" and row["session_id"] == "42"
    assert row["cost"] == pytest.approx(priced["cost"])


def test_stt_passes_estimated_cost_to_transport(monkeypatch):
    seen = {}
    def transport(provider, request, **kwargs):
        seen.update(kwargs)
        return "hello"
    monkeypatch.setattr("api.model_providers.service.transcribe", transport)
    text = voice_stt.transcribe(b"x", duration_ms=60_000)
    assert text == "hello"
    assert seen["usage_cost"] == pytest.approx(0.003)


# ── routes ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch, tmp_path):
    from api.voice_stt.routes import voice_stt_bp

    monkeypatch.setattr("api.http_authz.require_owner", lambda: ({"id": 7}, None))
    monkeypatch.setattr("api.voice_realtime.routes.current_user", lambda: {"id": 7})
    monkeypatch.setenv("CUTTLE_PROVIDER_CALLS_DB", str(tmp_path / "calls.db"))
    app = Flask(__name__)
    app.register_blueprint(voice_stt_bp)
    return app.test_client()


def test_realtime_settings_roundtrip(client, monkeypatch, tmp_path):
    import managers.settings_manager as settings_module
    from managers.settings_manager import SettingsManager

    isolated = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings_module, "get_settings_manager", lambda: isolated)
    r = client.get("/api/voice-stt/realtime/settings")
    assert r.status_code == 200 and r.json["success"]
    assert {m["id"] for m in r.json["models"]} >= {"gpt-realtime-2.1", "gpt-realtime-2.1-mini"}
    saved = client.post("/api/voice-stt/realtime/settings",
                        json={"voice_realtime": {"model": "gpt-realtime-2.1-mini", "voice": "cedar",
                                                 "transcription_model": "whisper-1"}})
    assert saved.json["voice_realtime"]["model"] == "gpt-realtime-2.1-mini"
    assert saved.json["voice_realtime"]["voice"] == "cedar"


def test_realtime_usage_route_prices_and_records(client, monkeypatch, tmp_path):
    r = client.post("/api/voice-stt/realtime/usage", json={
        "model": "gpt-realtime-2.1",
        "responses": [{"input_token_details": {"audio_tokens": 600},
                       "output_token_details": {"audio_tokens": 1200}}],
    })
    assert r.status_code == 200 and r.json["success"]
    assert r.json["usage"]["cost"] == pytest.approx((600 * 32 + 1200 * 64) / 1_000_000)


def test_duration_transcription_uses_selected_model():
    priced = voice_usage.price_realtime_payload("gpt-realtime-2.1", [],
        [{"type": "duration", "seconds": 60}], transcription_model="whisper-1")
    assert priced["transcription_cost"] == pytest.approx(.006)


def test_voice_receipts_are_idempotent_and_session_attributed(tmp_path, monkeypatch):
    db = tmp_path / "calls.db"
    monkeypatch.setenv("CUTTLE_PROVIDER_CALLS_DB", str(db))
    for _ in range(2):
        voice_usage.record_realtime_usage("gpt-realtime-2.1", [{"input_token_details": {"audio_tokens": 1000}}],
            session_id=42, usage_key="voice-42:response-1")
    assert len(provider_usage.all_calls()) == 1
    entries = voice_usage.session_entries([42])
    assert len(entries) == 1 and entries[0]["usage"]["cost"] == pytest.approx(.032)
    assert voice_usage.session_entries([43]) == []
