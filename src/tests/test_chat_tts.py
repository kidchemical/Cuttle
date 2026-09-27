"""Unit tests for chat TTS helpers (no live OpenAI calls)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api.chat_tts import (  # noqa: E402
    clean_text_for_speech,
    normalize_chat_tts_settings,
    prepare_spoken_text,
)


def test_normalize_defaults_and_rejects_bad_models():
    n = normalize_chat_tts_settings({})
    assert n["tts_model"] == "gpt-4o-mini-tts"
    assert n["summarize_model"] == "gpt-4o-mini"
    assert n["voice"] == "coral"

    bad = normalize_chat_tts_settings(
        {"tts_model": "not-a-model", "voice": "robot", "summarize_model": "gpt-9"}
    )
    assert bad["tts_model"] == "gpt-4o-mini-tts"
    assert bad["voice"] == "coral"
    assert bad["summarize_model"] == "gpt-4o-mini"

    ok = normalize_chat_tts_settings(
        {"tts_model": "tts-1", "voice": "alloy", "summarize_model": "o3-mini", "summarize": False}
    )
    assert ok["tts_model"] == "tts-1"
    assert ok["voice"] == "alloy"
    assert ok["summarize_model"] == "o3-mini"
    assert ok["summarize"] is False


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
    with patch("api.chat_tts.summarize_for_speech", return_value="Short spoken summary.") as mock_sum:
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
