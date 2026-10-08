"""Text-to-speech provider registry (TTS side of the Voice tab).

One row per provider — id, label, credential env names, models, curated
voices, tunable options, and transport — so adding a provider is a data
edit plus one small adapter, never a fork of the chat-voice stack. This
mirrors :mod:`api.completion_providers` (cheap LLM completions) on purpose:
Settings renders both pickers from a server registry instead of hardcoding
provider knowledge in HTML.

Providers:

* ``openai`` — ``/v1/audio/speech``. Fixed voice catalog, speed 0.25–4.0,
  no pitch. Billed per character.
* ``elevenlabs`` — ``/v1/text-to-speech/{voice_id}``. Voice ids are opaque;
  the curated list covers the well-known library voices and
  :func:`fetch_provider_voices` pulls the user's full library with their
  key. Speed 0.7–1.2 plus a stability (expressiveness) slider; no pitch.
  Billed per character, rate depends on the model.
* ``google`` — Gemini ``generateContent`` with ``speechConfig``. Fixed
  prebuilt voice catalog (no list endpoint — ``fetch_provider_voices``
  returns None). No speed or pitch knobs. Token-billed; the response
  carries real token usage, so cost is computed, not guessed.

Credential availability comes from the environment (``<home>/.env`` via the
daemon); providers without a key report ``configured: False`` instead of
raising at call time.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# List prices, verified 2026-10 against provider pricing pages.
# ElevenLabs bills per input character; OpenAI per input character;
# Google bills per token (response carries real usage).
ELEVENLABS_CHAR_RATES_USD_PER_MILLION = {
    "eleven_flash_v2_5": 50.0,
    "eleven_turbo_v2_5": 50.0,
    "eleven_flash_v2": 50.0,
    "eleven_turbo_v2": 50.0,
    "eleven_multilingual_v2": 100.0,
    "eleven_v3": 100.0,
}
OPENAI_CHAR_RATES_USD_PER_MILLION = {
    "tts-1": 15.0,
    "tts-1-hd": 30.0,
    # Per-token list price is $0.60/1M text in + $12/1M audio out;
    # this is the effective per-character approximation.
    "gpt-4o-mini-tts": 12.0,
}
GOOGLE_TOKEN_RATES_USD_PER_MILLION = {
    "gemini-2.5-flash-preview-tts": {"input": 0.50, "output": 10.0},
    "gemini-2.5-pro-preview-tts": {"input": 1.0, "output": 20.0},
}


class TtsProvider:
    """One speech backend. Data describing it; transport in ``synthesize``."""

    __slots__ = (
        "id", "label", "credential_env", "models", "default_model",
        "voices", "speed_range", "supports_stability",
    )

    def __init__(
        self,
        id: str,
        label: str,
        *,
        credential_env: Tuple[str, ...] = (),
        models: Tuple[Tuple[str, str], ...] = (),
        default_model: str = "",
        voices: Tuple[Tuple[str, str], ...] = (),
        speed_range: Optional[Tuple[float, float]] = None,
        supports_stability: bool = False,
    ) -> None:
        self.id = id
        self.label = label
        self.credential_env = credential_env
        self.models = models
        self.default_model = default_model
        self.voices = voices
        self.speed_range = speed_range
        self.supports_stability = supports_stability

    def credential_present(self) -> bool:
        return any((os.getenv(name) or "").strip() for name in self.credential_env)

    def configured(self) -> bool:
        return self.credential_present()

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "credential_env": list(self.credential_env),
            "credential_present": self.credential_present(),
            "configured": self.configured(),
            "models": [{"id": m, "label": lab} for m, lab in self.models],
            "default_model": self.default_model,
            "voices": [{"id": v, "label": lab} for v, lab in self.voices],
            "speed_range": list(self.speed_range) if self.speed_range else None,
            "supports_stability": self.supports_stability,
            "supports_fetch_voices": self.id == "elevenlabs",
        }


_REGISTRY: Tuple[TtsProvider, ...] = (
    TtsProvider(
        "openai",
        "OpenAI",
        credential_env=("OPENAI_API_KEY", "API_KEY"),
        models=(
            ("gpt-4o-mini-tts", "gpt-4o-mini-tts (recommended)"),
            ("tts-1", "tts-1 (fast / cheap)"),
            ("tts-1-hd", "tts-1-hd (higher quality)"),
        ),
        default_model="gpt-4o-mini-tts",
        voices=(
            ("coral", "coral"),
            ("alloy", "alloy"),
            ("ash", "ash"),
            ("ballad", "ballad"),
            ("echo", "echo"),
            ("fable", "fable"),
            ("nova", "nova"),
            ("onyx", "onyx"),
            ("sage", "sage"),
            ("shimmer", "shimmer"),
            ("verse", "verse"),
            ("marin", "marin"),
            ("cedar", "cedar"),
        ),
        speed_range=(0.25, 4.0),
    ),
    TtsProvider(
        "elevenlabs",
        "ElevenLabs",
        credential_env=("ELEVENLABS_API_KEY",),
        models=(
            ("eleven_flash_v2_5", "Flash v2.5 (fast, cheap)"),
            ("eleven_turbo_v2_5", "Turbo v2.5 (balanced)"),
            ("eleven_multilingual_v2", "Multilingual v2 (29 languages)"),
            ("eleven_v3", "Eleven v3 (most expressive, 70+ languages)"),
        ),
        default_model="eleven_flash_v2_5",
        voices=(
            ("21m00Tcm4TlvDq8ikWAM", "Rachel"),
            ("AZnzlk1XvdvUeBnXmlld", "Domi"),
            ("EXAVITQu4vr4xnSDxMaL", "Bella"),
            ("ErXwobaYiN019PkySvjV", "Antoni"),
            ("MF3mGyEYCl7XYWbNzoO", "Elli"),
            ("TxGEqnHWrfWFTfGW9XjX", "Josh"),
            ("VR6AewLTigWG4xSOOjQ", "Arnold"),
            ("pNInz6obpgDQGcFmaJgB", "Adam"),
            ("yoZ06aMxZJJJzPt9qdAB", "Sam"),
        ),
        speed_range=(0.7, 1.2),
        supports_stability=True,
    ),
    TtsProvider(
        "google",
        "Google",
        credential_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        models=(
            ("gemini-2.5-flash-preview-tts", "Flash TTS (fast, cheap)"),
            ("gemini-2.5-pro-preview-tts", "Pro TTS (higher quality)"),
        ),
        default_model="gemini-2.5-flash-preview-tts",
        voices=(
            ("Kore", "Kore"),
            ("Charon", "Charon"),
            ("Fenrir", "Fenrir"),
            ("Aoede", "Aoede"),
            ("Puck", "Puck"),
            ("Zephyr", "Zephyr"),
            ("Leda", "Leda"),
            ("Orus", "Orus"),
            ("Callirrhoe", "Callirrhoe"),
            ("Autonoe", "Autonoe"),
        ),
    ),
)

_BY_ID: Dict[str, TtsProvider] = {p.id: p for p in _REGISTRY}
PROVIDER_IDS: Tuple[str, ...] = tuple(p.id for p in _REGISTRY)


def get_provider(provider_id: Optional[str]) -> Optional[TtsProvider]:
    return _BY_ID.get(str(provider_id or "").strip().lower())


def list_providers() -> List[Dict[str, Any]]:
    """Public shape for the Settings UI."""
    return [p.to_public_dict() for p in _REGISTRY]


def normalize_model(provider_id: str, model: Optional[str]) -> str:
    """Curated model id, else the provider default (free-form ids pass)."""
    provider = get_provider(provider_id)
    if provider is None:
        return ""
    m = str(model or "").strip()
    if m:
        return m
    return provider.default_model


def normalize_voice(provider_id: str, voice: Optional[str]) -> str:
    """Curated voice id, a custom id (ElevenLabs voice_id / Google name),
    else the provider's first curated voice."""
    provider = get_provider(provider_id)
    if provider is None:
        return ""
    v = str(voice or "").strip()
    if v:
        return v
    return provider.voices[0][0] if provider.voices else ""


def _credential(provider_id: str) -> str:
    provider = get_provider(provider_id)
    if provider is None:
        raise ValueError(f"Unknown TTS provider: {provider_id}")
    for name in provider.credential_env:
        key = (os.environ.get(name) or "").strip()
        if key:
            return key
    raise RuntimeError(
        f"{provider.label} API key is not set "
        f"({' / '.join(provider.credential_env)})"
    )


def clamp_speed(provider_id: str, speed: Any) -> Optional[float]:
    provider = get_provider(provider_id)
    if provider is None or not provider.speed_range:
        return None
    try:
        v = float(speed if speed is not None else 1.0)
    except (TypeError, ValueError):
        v = 1.0
    lo, hi = provider.speed_range
    return max(lo, min(hi, v))


def clamp_stability(provider_id: str, stability: Any) -> Optional[float]:
    provider = get_provider(provider_id)
    if provider is None or not provider.supports_stability:
        return None
    try:
        v = float(stability if stability is not None else 0.5)
    except (TypeError, ValueError):
        v = 0.5
    return max(0.0, min(1.0, v))


def estimate_openai_cost_usd(model: str, chars: int) -> Optional[float]:
    rate = OPENAI_CHAR_RATES_USD_PER_MILLION.get(str(model or "").strip())
    if rate is None or (chars or 0) <= 0:
        return None
    return (int(chars) / 1_000_000) * float(rate)


def estimate_elevenlabs_cost_usd(model: str, chars: int) -> Optional[float]:
    rate = ELEVENLABS_CHAR_RATES_USD_PER_MILLION.get(str(model or "").strip())
    if rate is None or (chars or 0) <= 0:
        return None
    return (int(chars) / 1_000_000) * float(rate)


def estimate_google_cost_usd(
    model: str, input_tokens: int, output_tokens: int
) -> Optional[float]:
    rates = GOOGLE_TOKEN_RATES_USD_PER_MILLION.get(str(model or "").strip())
    if rates is None:
        return None
    try:
        cost = (int(input_tokens or 0) / 1_000_000) * float(rates["input"]) + (
            int(output_tokens or 0) / 1_000_000
        ) * float(rates["output"])
    except (TypeError, ValueError):
        return None
    return cost if cost > 0 else None


def estimate_cost_usd(
    provider_id: str,
    model: str,
    *,
    chars: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> Optional[float]:
    pid = str(provider_id or "").strip().lower()
    if pid == "openai":
        return estimate_openai_cost_usd(model, chars)
    if pid == "elevenlabs":
        return estimate_elevenlabs_cost_usd(model, chars)
    if pid == "google":
        return estimate_google_cost_usd(model, input_tokens, output_tokens)
    return None


def _synthesize_openai(
    text: str, *, voice: str, model: str, speed: Optional[float]
) -> Tuple[bytes, str, Dict[str, Any]]:
    from openai import OpenAI

    key = _credential("openai")
    # Hard caps from OpenAI docs (chars for tts-1*; tokens≈chars for mini-tts).
    limit = 2000 if model == "gpt-4o-mini-tts" else 4096
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip() or text[:limit]
    client = OpenAI(api_key=key)
    kwargs: Dict[str, Any] = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": "mp3",
    }
    if speed is not None:
        kwargs["speed"] = speed
    response = client.audio.speech.create(**kwargs)
    data = getattr(response, "content", None)
    if data is None and hasattr(response, "read"):
        data = response.read()
    if not data:
        raise RuntimeError("TTS returned empty audio")
    return bytes(data), "audio/mpeg", {}


def _synthesize_elevenlabs(
    text: str, *, voice: str, model: str, speed: Optional[float],
    stability: Optional[float],
) -> Tuple[bytes, str, Dict[str, Any]]:
    import requests

    key = _credential("elevenlabs")
    if len(text) > 5000:
        text = text[:5000].rsplit(" ", 1)[0].strip() or text[:5000]
    body: Dict[str, Any] = {"text": text, "model_id": model}
    voice_settings: Dict[str, Any] = {}
    if speed is not None:
        voice_settings["speed"] = speed
    if stability is not None:
        voice_settings["stability"] = stability
    if voice_settings:
        body["voice_settings"] = voice_settings
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        params={"output_format": "mp3_44100_128"},
        json=body,
        timeout=60,
    )
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs rejected the API key (401)")
    if r.status_code == 422:
        raise RuntimeError(f"ElevenLabs rejected the request (422): {r.text[:200]}")
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("TTS returned empty audio")
    return bytes(r.content), "audio/mpeg", {}


def _synthesize_google(
    text: str, *, voice: str, model: str
) -> Tuple[bytes, str, Dict[str, Any]]:
    import base64
    import requests

    provider = get_provider("google")
    assert provider is not None
    key = ""
    for name in provider.credential_env:
        key = (os.environ.get(name) or "").strip()
        if key:
            break
    if not key:
        raise RuntimeError(
            "Google API key is not set (GEMINI_API_KEY / GOOGLE_API_KEY)"
        )
    if len(text) > 8000:
        text = text[:8000].rsplit(" ", 1)[0].strip() or text[:8000]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                },
            },
        },
        timeout=60,
    )
    if r.status_code in (400, 401, 403):
        raise RuntimeError(f"Google TTS failed ({r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    payload = r.json()
    audio_b64 = ""
    mime = "audio/wav"
    try:
        for cand in payload.get("candidates", []):
            for part in (cand.get("content", {}) or {}).get("parts", []):
                inline = part.get("inlineData") or {}
                if inline.get("data"):
                    audio_b64 = inline["data"]
                    mime = str(inline.get("mimeType") or mime).split(";")[0]
                    break
    except (AttributeError, TypeError):
        pass
    if not audio_b64:
        raise RuntimeError(f"Google TTS returned no audio: {str(payload)[:200]}")
    use = payload.get("usageMetadata", {}) or {}
    extra = {
        "input_tokens": int(use.get("promptTokenCount") or 0),
        "output_tokens": int(use.get("candidatesTokenCount") or 0),
    }
    return base64.b64decode(audio_b64), mime, extra


def synthesize(
    provider_id: str,
    text: str,
    *,
    voice: Optional[str] = None,
    model: Optional[str] = None,
    speed: Any = None,
    stability: Any = None,
) -> Tuple[bytes, str, Dict[str, Any]]:
    """Return (audio bytes, content type, extra usage hints).

    Raises ValueError for empty text, RuntimeError for credential or
    provider failures. Extra carries provider-native usage the caller
    folds into cost (Google token counts; {} elsewhere).
    """
    provider = get_provider(provider_id)
    if provider is None:
        raise ValueError(f"Unknown TTS provider: {provider_id}")
    spoken = str(text or "").strip()
    if not spoken:
        raise ValueError("Nothing to speak")
    pid = provider.id
    voice_id = normalize_voice(pid, voice)
    model_id = normalize_model(pid, model)
    if pid == "openai":
        return _synthesize_openai(
            spoken, voice=voice_id, model=model_id,
            speed=clamp_speed(pid, speed),
        )
    if pid == "elevenlabs":
        return _synthesize_elevenlabs(
            spoken, voice=voice_id, model=model_id,
            speed=clamp_speed(pid, speed),
            stability=clamp_stability(pid, stability),
        )
    if pid == "google":
        return _synthesize_google(spoken, voice=voice_id, model=model_id)
    raise ValueError(f"Unknown TTS provider: {provider_id}")


def fetch_provider_voices(provider_id: str) -> Optional[List[Dict[str, str]]]:
    """The user's own voice library via their stored key.

    Returns None when the provider has no list endpoint (Google's
    prebuilt catalog is fixed — the curated list is the whole menu).
    """
    pid = str(provider_id or "").strip().lower()
    if pid != "elevenlabs":
        return None
    import requests

    key = _credential("elevenlabs")
    r = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": key},
        timeout=30,
    )
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs rejected the API key (401)")
    r.raise_for_status()
    out = []
    for v in (r.json().get("voices", []) or []):
        vid = str(v.get("voice_id") or "").strip()
        name = str(v.get("name") or vid).strip()
        if vid:
            out.append({"id": vid, "label": name})
    return out


def check_key(provider_id: str, api_key: str) -> Tuple[bool, str]:
    """Validate a pasted key with one cheap authenticated read (no synthesis).

    Backs Settings → Providers "Test"; returns ``(ok, message)`` so the
    route stays a thin transport.
    """
    import requests

    pid = str(provider_id or "").strip().lower()
    if pid == "elevenlabs":
        if not api_key or len(api_key) < 20:
            return False, "Invalid key format. ElevenLabs keys are long hex strings"
        try:
            r = requests.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": api_key},
                timeout=10,
            )
        except Exception as e:
            return False, f"ElevenLabs API error: {e}"
        if r.status_code == 200:
            sub = r.json().get("subscription", {}) or {}
            return True, f"Valid ElevenLabs key (plan: {sub.get('tier', 'unknown')})"
        if r.status_code == 401:
            return False, "Invalid ElevenLabs API key"
        return False, f"ElevenLabs API error: HTTP {r.status_code}"
    if pid == "google":
        if not api_key or len(api_key) < 20:
            return False, 'Invalid key format. Google AI Studio keys start with "AIza"'
        try:
            r = requests.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key, "pageSize": 1},
                timeout=10,
            )
        except Exception as e:
            return False, f"Google API error: {e}"
        if r.status_code == 200:
            return True, "Valid Google API key"
        if r.status_code in (400, 401, 403):
            return False, "Invalid Google API key"
        return False, f"Google API error: HTTP {r.status_code}"
    return False, f"Unknown TTS provider: {provider_id}"
