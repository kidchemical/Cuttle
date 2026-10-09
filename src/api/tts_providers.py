"""Speech settings compatibility facade and cost estimates.

Provider declarations and every transport live in api.model_providers.
This facade preserves established chat_tts preferences and normalization APIs.
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
            "supports_fetch_voices": next(p for p in manifests() if p.id == self.id).speech.get("fetch_voices", False),
        }


from api.model_providers.catalog import manifests

_REGISTRY: Tuple[TtsProvider, ...] = tuple(
    TtsProvider(p.id, p.label, credential_env=p.credential_env,
                models=tuple((m['id'], m.get('label') or m['id']) for m in p.speech.get('models', [])),
                default_model=p.speech['default_model'],
                voices=tuple((v['id'], v.get('label') or v['id']) for v in p.speech.get('voices', [])),
                speed_range=tuple(p.speech['speed_range']) if p.speech.get('speed_range') else None,
                supports_stability=p.speech.get('supports_stability', False))
    for p in manifests() if 'speech_generation' in p.operations
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


def normalize_voice(provider_id: str, voice: Optional[str], model: Optional[str] = None) -> str:
    """Curated voice id, a custom id (ElevenLabs voice_id / Google name),
    else the provider's first curated voice."""
    provider = get_provider(provider_id)
    if provider is None:
        return ""
    v = str(voice or "").strip()
    if v:
        return v
    from api.model_providers.catalog import get_provider as manifest_for, get_adapter
    manifest = manifest_for(provider_id)
    if manifest and not manifest.speech.get("fetch_voices"):
        voices = get_adapter(provider_id).list_voices(manifest, model or provider.default_model)
        return voices[0]["id"] if voices else ""
    return provider.voices[0][0] if provider.voices else ""


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



def synthesize(provider_id, text, *, voice=None, model=None, speed=None, stability=None):
    from api.model_providers.service import synthesize_speech
    from api.model_providers.types import SpeechRequest
    if get_provider(provider_id) is None:
        raise ValueError(f"Unknown TTS provider: {provider_id}")
    text = str(text or "").strip()
    if not text:
        raise ValueError("Nothing to speak")
    model_id = normalize_model(provider_id, model)
    try:
        tts_cost = estimate_cost_usd(provider_id, model_id, chars=len(text))
    except Exception:
        tts_cost = None
    result = synthesize_speech(provider_id, SpeechRequest(text, model_id,
                              normalize_voice(provider_id, voice, model_id), clamp_speed(provider_id, speed),
                              clamp_stability(provider_id, stability)),
                              usage_task="tts", usage_cost=tts_cost)
    return result.audio, result.mime, result.usage


def fetch_provider_voices(provider_id):
    from api.model_providers.service import discover_voices
    return discover_voices(provider_id, refresh=True)['voices']


def check_key(provider_id, api_key):
    from api.model_providers.service import check_key as probe
    return probe(provider_id, api_key)
