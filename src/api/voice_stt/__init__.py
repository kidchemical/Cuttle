"""Voice-mode server transcription (experimental ``voice_server_stt``).

The browser records continuously and uploads one audio clip per spoken phrase;
this owner turns each clip into text with OpenAI transcription. Replaces the
Web Speech recognizer, whose start/stop chime fires on Android every time it
restarts after a pause.
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


def _client():
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI

    return OpenAI(api_key=key, timeout=30.0)


def transcribe(
    audio: bytes,
    *,
    mime: str = "audio/webm",
    prompt: str = "",
    language: str = "",
    duration_ms: Optional[int] = None,
) -> str:
    """Return the phrase text ("" when nothing intelligible was said).

    Raises ``ValueError`` for an empty/oversized clip, ``RuntimeError`` when
    transcription is unavailable.
    """
    if not audio:
        raise ValueError("audio is empty")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("audio clip is too large")
    client = _client()
    kwargs = {"file": (f"phrase.{file_extension(mime)}", audio, mime or "audio/webm")}
    hint = re.sub(r"\s+", " ", str(prompt or "")).strip()[-MAX_PROMPT_CHARS:]
    if hint:
        kwargs["prompt"] = hint
    lang = str(language or "").split("-", 1)[0].strip().lower()
    if re.fullmatch(r"[a-z]{2}", lang):
        kwargs["language"] = lang
    last_error: Optional[Exception] = None
    for model in (DEFAULT_MODEL, FALLBACK_MODEL):
        try:
            result = client.audio.transcriptions.create(model=model, **kwargs)
        except Exception as exc:  # model unavailable on this key → try the fallback
            last_error = exc
            continue
        return clean_transcript(getattr(result, "text", "") or "", duration_ms=duration_ms)
    raise RuntimeError(f"transcription failed: {type(last_error).__name__}")
