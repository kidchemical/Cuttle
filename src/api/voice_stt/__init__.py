"""Server speech-to-text for voice mode and composer voice clips.

The browser uploads one audio clip per spoken phrase (voice mode with
``voice_server_stt``, composer clips with ``composer_voice_clip``) or a whole
voice clip (attachment pre-pass); this owner turns it into text with the
provider chosen in Settings → Voice → Speech to text (``voice_stt.settings``).
"""

from __future__ import annotations

import os
import re
from typing import Optional

FLAG_ID = "voice_server_stt"

DEFAULT_MODEL = "gpt-4o-mini-transcribe"
FALLBACK_MODEL = "whisper-1"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_PROMPT_CHARS = 400

_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/aac": "aac",
}

# Whisper-family models answer near-silent clips with stock phrases.
_SILENCE_HALLUCINATIONS = frozenset({
    "you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye",
})


def is_enabled() -> bool:
    from api.experimental import is_enabled as flag_enabled

    return flag_enabled(FLAG_ID)


def configured() -> bool:
    """True when the chosen speech-to-text provider can run on this server."""
    from api.voice_stt import settings

    return settings.configured()


def clips_enabled() -> bool:
    from api.experimental import is_enabled as flag_enabled

    return flag_enabled("composer_voice_clip")


def file_extension(mime: str) -> str:
    base = str(mime or "").split(";", 1)[0].strip().lower()
    return _EXTENSIONS.get(base, "webm")


def clean_transcript(text: str, *, duration_ms: Optional[int] = None) -> str:
    """Normalize whitespace; drop stock hallucinations on very short clips."""
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    key = out.lower().strip(" .!?,")
    short = duration_ms is not None and duration_ms < 1500
    if not key or (short and key in _SILENCE_HALLUCINATIONS):
        return ""
    return out


def transcribe(
    audio: bytes,
    *,
    mime: str = "audio/webm",
    prompt: str = "",
    language: str = "",
    duration_ms: Optional[int] = None,
    max_bytes: int = MAX_AUDIO_BYTES,
) -> str:
    """Return the phrase text ("" when nothing intelligible was said).

    Raises ``ValueError`` for an empty/oversized clip, ``RuntimeError`` when
    transcription is unavailable.
    """
    if not audio:
        raise ValueError("audio is empty")
    if len(audio) > max_bytes:
        raise ValueError("audio clip is too large")
    from api.model_providers.service import transcribe as provider_transcribe
    from api.model_providers.types import TranscriptionRequest
    kwargs = {}
    hint = re.sub(r"\s+", " ", str(prompt or "")).strip()[-MAX_PROMPT_CHARS:]
    if hint:
        kwargs["prompt"] = hint
    lang = str(language or "").split("-", 1)[0].strip().lower()
    if re.fullmatch(r"[a-z]{2}", lang):
        kwargs["language"] = lang
    last_error: Optional[Exception] = None
    from api import voice_usage
    from api.voice_stt import settings

    provider, models, base_url = settings.resolve()
    if provider == settings.BROWSER:
        raise RuntimeError("speech to text is set to the browser's recognizer, which cannot read a recording")
    if base_url:
        kwargs["base_url"] = base_url
    for model in models:
        seconds = (duration_ms / 1000.0) if duration_ms else None
        cost = voice_usage.estimate_transcription_cost_usd(model, seconds=seconds)
        try:
            result = provider_transcribe(provider, TranscriptionRequest(audio, model,
                        filename=f"phrase.{file_extension(mime)}", mime=mime or "audio/webm", **kwargs),
                        usage_task="stt", usage_cost=cost)
        except Exception as exc:  # model unavailable on this key → try the next one
            last_error = exc
            continue
        return clean_transcript(result or "", duration_ms=duration_ms)
    if isinstance(last_error, RuntimeError) and "API key is not set" in str(last_error):
        raise RuntimeError(f"no API key for the {provider} speech-to-text provider (Settings → Voice → Speech to text)")
    raise RuntimeError(f"transcription failed ({provider}): {type(last_error).__name__}")
