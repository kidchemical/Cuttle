"""Spoken sample of a Realtime voice for Settings → Voice.

This is a real Realtime call, not a TTS stand-in: one short text-in/audio-out
response from the exact model and voice a conversation would use. It costs a
fraction of a cent, is recorded in the provider ledger, and is cached with the
other voice clips so replaying a voice is free.
"""
from __future__ import annotations

import base64
import io
import threading
import uuid
import wave

from api import voice_cache, voice_usage
from api.voice_realtime import REALTIME_MODELS, REALTIME_VOICES
from api.voice_realtime.service import _realtime_client

PREVIEW_TEXT = "Hi! This is how I will sound in a live voice conversation."
RATE = 24000
TIMEOUT_SEC = 20
MAX_PCM_BYTES = RATE * 2 * 20  # 20 s of mono PCM16 is far beyond one sentence


def synthesize(model, voice):
    """Return ``(wav_bytes, content_type, meta)``; ``meta`` has cached/cost."""
    if model not in {value[0] for value in REALTIME_MODELS}:
        raise ValueError("Choose a listed Realtime model.")
    if voice not in {value[0] for value in REALTIME_VOICES}:
        raise ValueError("Choose a listed Realtime voice.")
    key = voice_cache.cache_key("openai-realtime", model, voice, None, None, PREVIEW_TEXT)
    hit = voice_cache.get(key)
    if hit:
        return hit[0], hit[1], {"cached": True, "cost": None}
    pcm, usage, response_id = _speak(model, voice)
    audio = _wav(pcm)
    cost = None
    if isinstance(usage, dict):
        priced = voice_usage.record_realtime_usage(
            model, [usage], usage_key="realtime-preview:" + (response_id or uuid.uuid4().hex))
        cost = priced.get("cost")
    voice_cache.put(key, audio, "audio/wav", {"provider": "openai-realtime", "model": model, "voice": voice})
    return audio, "audio/wav", {"cached": False, "cost": cost}


def _event(raw):
    return raw if isinstance(raw, dict) else (raw.model_dump() if hasattr(raw, "model_dump") else dict(raw))


def _speak(model, voice):
    chunks, size, usage, response_id, error = [], 0, None, "", ""
    with _realtime_client() as sdk:
        with sdk.realtime.connect(model=model) as connection:
            # The socket iterator blocks; closing it is the only reliable deadline.
            expired = threading.Event()
            timer = threading.Timer(TIMEOUT_SEC, lambda: (expired.set(), connection.close()))
            timer.daemon = True
            timer.start()
            try:
                connection.send({"type": "session.update", "session": {
                    "type": "realtime", "output_modalities": ["audio"],
                    "instructions": "Read the user's message aloud exactly as written. Add nothing before or after it.",
                    "audio": {"output": {"voice": voice, "format": {"type": "audio/pcm", "rate": RATE}}}}})
                connection.send({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": PREVIEW_TEXT}]}})
                connection.send({"type": "response.create"})
                for raw in connection:
                    event = _event(raw)
                    kind = event.get("type")
                    if kind == "response.output_audio.delta":
                        chunk = base64.b64decode(event.get("delta") or "")
                        size += len(chunk)
                        if size > MAX_PCM_BYTES:
                            break
                        chunks.append(chunk)
                    elif kind == "response.done":
                        response = event.get("response") or {}
                        usage, response_id = response.get("usage"), str(response.get("id") or "")
                        break
                    elif kind == "error":
                        error = str((event.get("error") or {}).get("message") or "provider error")
                        break
            finally:
                timer.cancel()
    if error:
        raise RuntimeError("Realtime preview failed: " + error)
    if expired.is_set() and usage is None:
        raise RuntimeError("Realtime preview timed out; try again.")
    if not chunks:
        raise RuntimeError("Realtime returned no audio for the preview; try again.")
    return b"".join(chunks), usage, response_id


def _wav(pcm):
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(RATE)
        clip.writeframes(pcm)
    return out.getvalue()
