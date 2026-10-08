"""Unit tests for chat TTS pipeline (no live provider calls)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api.chat_tts import (  # noqa: E402
    clean_text_for_speech,
    effective_summarize_model,
    effective_tts_model,
    effective_tts_voice,
    estimate_tts_cost_usd,
    normalize_chat_tts_settings,
    prepare_spoken_text,
    prepare_spoken_text_with_usage,
    resolve_speech_settings,
    summarize_for_speech_with_usage,
    voice_layer_usage,
)


def test_normalize_defaults_to_openai_shape():
    n = normalize_chat_tts_settings({})
    assert n["provider"] == "openai"
    assert n["models"] == {}
    assert n["voices"] == {}
    assert n["summarize_provider"] == "openai"
    assert n["summarize_models"] == {}
    assert n["speed"] == 1.0


def test_normalize_accepts_per_provider_maps_and_freeform_models():
    n = normalize_chat_tts_settings({
        "provider": "elevenlabs",
        "models": {"elevenlabs": "eleven_v3", "bogus": "x"},
        "voices": {"elevenlabs": "21m00Tcm4TlvDq8ikWAM"},
        "speed": 1.1,
        "stability": 0.3,
        "summarize_provider": "anthropic",
        "summarize_models": {"anthropic": "claude-haiku-4-5-20251001"},
    })
    assert n["provider"] == "elevenlabs"
    assert n["models"] == {"elevenlabs": "eleven_v3"}
    assert n["voices"] == {"elevenlabs": "21m00Tcm4TlvDq8ikWAM"}
    assert n["speed"] == pytest.approx(1.1)
    assert n["summarize_provider"] == "anthropic"
    assert n["summarize_models"] == {"anthropic": "claude-haiku-4-5-20251001"}


def test_normalize_migrates_legacy_flat_keys():
    n = normalize_chat_tts_settings(
        {"tts_model": "tts-1", "voice": "alloy", "summarize_model": "o3-mini"}
    )
    assert n["provider"] == "openai"
    assert n["models"] == {"openai": "tts-1"}
    assert n["voices"] == {"openai": "alloy"}
    assert n["summarize_models"] == {"openai": "o3-mini"}


def test_normalize_rejects_unknown_providers_and_clamps_tunables():
    n = normalize_chat_tts_settings(
        {"provider": "nope", "speed": 99, "stability": -3,
         "summarize_provider": "nope"}
    )
    assert n["provider"] == "openai"
    assert n["speed"] == 4.0
    assert n["stability"] == 0.0
    assert n["summarize_provider"] == "openai"


def test_effective_resolution_prefers_saved_then_default():
    settings = normalize_chat_tts_settings(
        {"voices": {"elevenlabs": "abc123"}, "summarize_models": {"anthropic": "my-model"}}
    )
    assert effective_tts_voice(settings, "elevenlabs") == "abc123"
    assert effective_tts_voice(settings, "openai") == "coral"
    assert effective_tts_model(settings, "google") == "gemini-2.5-flash-preview-tts"
    assert effective_summarize_model(settings, "anthropic") == "my-model"


def test_resolve_speech_settings_applies_overrides():
    settings = normalize_chat_tts_settings({})
    r = resolve_speech_settings(settings, {
        "provider": "elevenlabs", "voice": "abc", "speed": 5, "stability": 0.2,
    })
    assert r["provider"] == "elevenlabs"
    assert r["voice"] == "abc"
    assert r["model"] == "eleven_flash_v2_5"
    assert r["speed"] == 1.2  # clamped to the provider range
    assert r["stability"] == pytest.approx(0.2)
    g = resolve_speech_settings(settings, {"provider": "google"})
    assert g["speed"] is None and g["stability"] is None
    with pytest.raises(ValueError):
        resolve_speech_settings(settings, {"provider": "nope"})


def test_clean_strips_cuttle_chrome_and_markdown():
    raw = (
        "Hello **world**\n"
        "<cuttle_action_form>{\"mode\":\"choice\"}</cuttle_action_form>\n"
        "See [docs](https://example.com) and `code`.\n"
        "```\nsecret\n```\n"
    )
    out = clean_text_for_speech(raw)
    assert "cuttle_action_form" not in out
    assert "https://example.com" not in out
    assert "secret" not in out
    assert "Hello" in out
    assert "docs" in out


def test_clean_strips_think_blocks_entirely():
    raw = (
        "<think>\nStep 1: poke around the bubble CSS.\n"
        "I should not be spoken aloud.\n</think>\n"
        "Here is the real answer for the user."
    )
    out = clean_text_for_speech(raw)
    assert "poke around" not in out
    assert "spoken aloud" not in out
    assert "real answer" in out


def test_clean_strips_redacted_thinking_blocks():
    raw = (
        "<redacted_thinking>internal plan</redacted_thinking>\n"
        "Visible reply only."
    )
    out = clean_text_for_speech(raw)
    assert "internal plan" not in out
    assert "Visible reply" in out


def test_prepare_skips_summarize_when_short():
    settings = normalize_chat_tts_settings({"summarize": True, "skip_summarize_under_chars": 380})
    short = "This is a short reply."
    spoken, did = prepare_spoken_text(short, settings)
    assert did is False
    assert "short reply" in spoken


def test_prepare_calls_summarizer_when_long():
    settings = normalize_chat_tts_settings(
        {"summarize": True, "skip_summarize_under_chars": 50, "target_spoken_chars": 120}
    )
    long_text = "Word " * 80
    with patch(
        "api.chat_tts.summarize_for_speech_with_usage",
        return_value=("Short spoken summary.", {}),
    ) as mock_sum:
        spoken, did = prepare_spoken_text(long_text, settings)
    assert did is True
    assert spoken == "Short spoken summary."
    mock_sum.assert_called_once()


def test_prepare_force_no_summarize():
    settings = normalize_chat_tts_settings({"summarize": True, "skip_summarize_under_chars": 10})
    long_text = "Word " * 40
    with patch("api.chat_tts.summarize_for_speech") as mock_sum:
        spoken, did = prepare_spoken_text(long_text, settings, force_summarize=False)
    assert did is False
    mock_sum.assert_not_called()
    assert "Word" in spoken


def test_prepare_falls_back_to_full_text_when_narrator_fails():
    settings = normalize_chat_tts_settings(
        {"summarize": True, "skip_summarize_under_chars": 10,
         "summarize_provider": "anthropic"}
    )
    with patch(
        "api.chat_tts.summarize_for_speech_with_usage",
        side_effect=RuntimeError("ANTHROPIC_API_KEY is not set"),
    ):
        spoken, did, usage = prepare_spoken_text_with_usage("Word " * 40, settings)
    assert did is False
    assert "Word" in spoken
    assert usage == {}


def test_estimate_tts_cost_usd_per_provider_rates():
    assert estimate_tts_cost_usd("openai", "tts-1", 1_000_000) == pytest.approx(15.0)
    assert estimate_tts_cost_usd("openai", "gpt-4o-mini-tts", 1_000_000) == pytest.approx(12.0)
    assert estimate_tts_cost_usd("elevenlabs", "eleven_flash_v2_5", 1_000_000) == pytest.approx(50.0)
    assert estimate_tts_cost_usd("elevenlabs", "eleven_multilingual_v2", 1_000_000) == pytest.approx(100.0)
    assert estimate_tts_cost_usd(
        "google", "gemini-2.5-flash-preview-tts", 0,
        input_tokens=1000, output_tokens=5000,
    ) == pytest.approx(0.0005 + 0.05)
    assert estimate_tts_cost_usd("openai", "tts-1", 0) is None
    assert estimate_tts_cost_usd("nope", "x", 500) is None


def test_voice_layer_usage_folds_summarize_and_tts():
    out = voice_layer_usage(
        {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
         "model": "gpt-4o-mini", "cost": 0.0001, "cost_estimated": True},
        provider="openai",
        tts_model="tts-1",
        tts_chars=1000,
    )
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 20
    assert out["tts_provider"] == "openai"
    assert out["tts_chars"] == 1000
    assert out["tts_model"] == "tts-1"
    assert out["tts_cost"] == pytest.approx(0.015)
    assert out["cost"] == pytest.approx(0.0001 + 0.015)
    assert out["cost_estimated"] is True


def test_voice_layer_usage_without_summarize_is_tts_only():
    out = voice_layer_usage({}, provider="openai", tts_model="tts-1-hd", tts_chars=2000)
    assert out["prompt_tokens"] == 0
    assert out["completion_tokens"] == 0
    assert out["cost"] == pytest.approx(0.06)
    assert out["cost_estimated"] is True
    assert "summarize_cost" not in out


def test_summarize_openai_reports_step_tokens():
    fake_usage = MagicMock()
    fake_usage.prompt_tokens = 500
    fake_usage.completion_tokens = 60
    fake_usage.total_tokens = 560
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="Short spoken summary."))]
    fake_resp.usage = fake_usage
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp
    with patch("api.chat_tts._openai_client", return_value=fake_client), patch(
        "api.model_pricing.enrich_usage_for_display", side_effect=lambda u, model=None: u
    ):
        spoken, usage = summarize_for_speech_with_usage(
            "Word " * 100, provider="openai", model="gpt-4o-mini", target_chars=120
        )
    assert spoken == "Short spoken summary."
    assert usage["prompt_tokens"] == 500
    assert usage["completion_tokens"] == 60
    assert usage["model"] == "gpt-4o-mini"


def test_summarize_anthropic_reports_step_tokens():
    block = MagicMock()
    block.type = "text"
    block.text = "Short spoken summary."
    fake_resp = MagicMock()
    fake_resp.content = [block]
    fake_resp.usage = MagicMock(input_tokens=400, output_tokens=50)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp
    with patch("api.chat_tts._anthropic_client", return_value=fake_client), patch(
        "api.model_pricing.enrich_usage_for_display", side_effect=lambda u, model=None: u
    ):
        spoken, usage = summarize_for_speech_with_usage(
            "Word " * 100, provider="anthropic",
            model="claude-haiku-4-5-20251001", target_chars=120,
        )
    assert spoken == "Short spoken summary."
    assert usage["prompt_tokens"] == 400
    assert usage["completion_tokens"] == 50
