"""Chat bubble text-to-speech (provider-modular).

On demand: optional narrator summary (OpenAI or Anthropic, any model id)
→ provider TTS (OpenAI, ElevenLabs, Google) → audio bytes, with a 7-day
server-side clip cache. Settings live in settings.json under ``chat_tts``.
Provider knowledge (voices, models, rates, transport) is owned by
:mod:`api.tts_providers`; this module owns settings shape, the speak
pipeline, and the Flask routes.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request

from api.http_authz import authenticated_required, owner_required
from api import tts_providers as providers

# Narrator (speak-summary) backends. The model id itself is free-form — any
# model the user's key can reach — with registry suggestions in the UI.
SUMMARIZE_PROVIDERS = ("openai", "anthropic")

DEFAULT_CHAT_TTS: Dict[str, Any] = {
    "enabled": True,
    "provider": "openai",
    "models": {},
    "voices": {},
    "speed": 1.0,
    "stability": 0.5,
    "summarize": True,
    "summarize_provider": "openai",
    "summarize_models": {},
    "target_spoken_chars": 420,
    "skip_summarize_under_chars": 380,
    "max_input_chars": 24000,
}

_CUTTLE_STRIP_RE = re.compile(
    r"<cuttle_(?:ui_capabilities|action_form(?:_pending)?|widget|supervised_activity|trace|form)"
    r"\b[^>]*>[\s\S]*?</cuttle_(?:ui_capabilities|action_form(?:_pending)?|widget|supervised_activity|trace|form)\s*>",
    re.IGNORECASE,
)
_CUTTLE_CONFIRM_RE = re.compile(
    r"<cuttle_confirm(?:_pending)?\b[^>]*>([\s\S]*?)</cuttle_confirm(?:_pending)?>",
    re.IGNORECASE,
)
_CUTTLE_BUTTON_RE = re.compile(
    r"<cuttle_button\b[^>]*/>|<cuttle_button\b[^>]*>[\s\S]*?</cuttle_button>",
    re.IGNORECASE,
)
# Reasoning must be removed entirely — stripping only the tags leaves the body
# in the speakable string (and then in the Transcript section).
_THINK_BLOCK_RE = re.compile(
    r"<(think|redacted_thinking)\b[^>]*>[\s\S]*?</\1\s*>",
    re.IGNORECASE,
)
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def default_chat_tts_settings() -> Dict[str, Any]:
    return dict(DEFAULT_CHAT_TTS)


def _str_map(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v).strip() for k, v in raw.items() if str(v or "").strip()}


def effective_tts_model(settings: Dict[str, Any], provider: str) -> str:
    return providers.normalize_model(
        provider, (settings.get("models") or {}).get(provider)
    )


def effective_tts_voice(settings: Dict[str, Any], provider: str) -> str:
    return providers.normalize_voice(
        provider, (settings.get("voices") or {}).get(provider)
    )


def effective_summarize_model(settings: Dict[str, Any], provider: str) -> str:
    """Narrator model: explicit per-provider id, else the registry default."""
    from api.completion_providers import resolve_model

    explicit = (settings.get("summarize_models") or {}).get(provider, "")
    if str(explicit or "").strip():
        return str(explicit).strip()
    return resolve_model(provider)


def normalize_chat_tts_settings(raw: Any) -> Dict[str, Any]:
    out = default_chat_tts_settings()
    if not isinstance(raw, dict):
        # Migrate the pre-provider shape: {tts_model, voice, summarize_model}.
        return out

    if "enabled" in raw:
        out["enabled"] = bool(raw.get("enabled"))
    if "summarize" in raw:
        out["summarize"] = bool(raw.get("summarize"))

    provider = str(raw.get("provider") or "").strip().lower()
    if provider in providers.PROVIDER_IDS:
        out["provider"] = provider
    models = _str_map(raw.get("models"))
    if models:
        out["models"] = {k: v for k, v in models.items() if k in providers.PROVIDER_IDS}
    voices = _str_map(raw.get("voices"))
    if voices:
        out["voices"] = {k: v for k, v in voices.items() if k in providers.PROVIDER_IDS}
    # Migrate legacy flat keys onto the OpenAI row.
    legacy_model = str(raw.get("tts_model") or "").strip()
    if legacy_model and "openai" not in out["models"]:
        out["models"] = {**out["models"], "openai": legacy_model}
    legacy_voice = str(raw.get("voice") or "").strip()
    if legacy_voice and "openai" not in out["voices"]:
        out["voices"] = {**out["voices"], "openai": legacy_voice}

    try:
        out["speed"] = max(
            0.25, min(4.0, float(raw.get("speed", out["speed"])))
        )
    except (TypeError, ValueError):
        pass
    try:
        out["stability"] = max(0.0, min(1.0, float(raw.get("stability", out["stability"]))))
    except (TypeError, ValueError):
        pass

    sprovider = str(raw.get("summarize_provider") or "").strip().lower()
    if sprovider in SUMMARIZE_PROVIDERS:
        out["summarize_provider"] = sprovider
    smodels = _str_map(raw.get("summarize_models"))
    if smodels:
        out["summarize_models"] = {
            k: v for k, v in smodels.items() if k in SUMMARIZE_PROVIDERS
        }
    legacy_smodel = str(raw.get("summarize_model") or "").strip()
    if legacy_smodel and "openai" not in out["summarize_models"]:
        out["summarize_models"] = {**out["summarize_models"], "openai": legacy_smodel}

    for key, lo, hi in (
        ("target_spoken_chars", 80, 2000),
        ("skip_summarize_under_chars", 40, 4000),
        ("max_input_chars", 1000, 100000),
    ):
        if key not in raw:
            continue
        try:
            n = int(raw[key])
            out[key] = max(lo, min(hi, n))
        except (TypeError, ValueError):
            pass
    return out


def load_chat_tts_settings() -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    return normalize_chat_tts_settings(sm.get_setting("chat_tts"))


def save_chat_tts_settings(partial: Dict[str, Any]) -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    current = load_chat_tts_settings()
    if isinstance(partial, dict):
        merged = {**current, **partial}
    else:
        merged = current
    normalized = normalize_chat_tts_settings(merged)
    sm.set_setting("chat_tts", normalized)
    return normalized


def clean_text_for_speech(text: str) -> str:
    """Strip UI chrome / markdown / reasoning so TTS does not read them aloud."""
    s = str(text or "")
    s = _THINK_BLOCK_RE.sub("", s)
    s = _CUTTLE_STRIP_RE.sub("", s)
    s = _CUTTLE_CONFIRM_RE.sub(r"\1", s)
    s = _CUTTLE_BUTTON_RE.sub("", s)
    s = _CODE_FENCE_RE.sub(" ", s)
    s = _MD_IMAGE_RE.sub(r"\1", s)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = re.sub(r"[#*_`>~]+", " ", s)
    s = re.sub(r"(?m)^\s*[-•]\s+", "", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _openai_client():
    import os

    key = (os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai package not installed") from e
    return OpenAI(api_key=key)


def _anthropic_client():
    import os

    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError("anthropic package not installed") from e
    return Anthropic(api_key=key)


def estimate_tts_cost_usd(
    provider: str,
    model: str,
    chars: int,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> Optional[float]:
    """Estimate USD for one speech synthesis (delegates to the registry)."""
    return providers.estimate_cost_usd(
        provider, model, chars=chars,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )


def _summarize_system(target: int) -> str:
    return (
        "You rewrite assistant chat replies for text-to-speech. "
        "Output only plain spoken sentences a listener can follow. "
        "No markdown, bullets, code, URLs, file paths, or UI chrome. "
        f"Aim for about {target} characters (roughly 60–90 words). "
        "Keep the key points; drop boilerplate and apologies."
    )


def _enrich_usage(usage: Dict[str, Any], model: str) -> Dict[str, Any]:
    try:
        from api.model_pricing import enrich_usage_for_display

        enriched = enrich_usage_for_display(dict(usage), model=str(model))
        if enriched:
            return enriched
    except Exception:
        pass
    return usage


def _summarize_via_openai(
    snippet: str, system: str, model: str
) -> Tuple[str, Dict[str, Any]]:
    client = _openai_client()
    create_kw: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": snippet},
        ],
    }
    # Reasoning mini models reject temperature / max_tokens.
    if str(model).startswith(("o1", "o3", "o4")):
        create_kw["max_completion_tokens"] = 280
    else:
        create_kw["temperature"] = 0.3
        create_kw["max_tokens"] = 280
    resp = client.chat.completions.create(**create_kw)
    text = (resp.choices[0].message.content or "").strip()
    usage: Dict[str, Any] = {"model": str(model)}
    try:
        ru = getattr(resp, "usage", None)
        if ru is not None:
            pt = int(getattr(ru, "prompt_tokens", 0) or 0)
            ct = int(getattr(ru, "completion_tokens", 0) or 0)
            if pt or ct:
                usage["prompt_tokens"] = pt
                usage["completion_tokens"] = ct
                usage["total_tokens"] = int(getattr(ru, "total_tokens", 0) or (pt + ct))
    except (TypeError, ValueError):
        pass
    return text, _enrich_usage(usage, model)


def _summarize_via_anthropic(
    snippet: str, system: str, model: str
) -> Tuple[str, Dict[str, Any]]:
    client = _anthropic_client()
    resp = client.messages.create(
        model=model,
        max_tokens=280,
        temperature=0.3,
        system=system,
        messages=[{"role": "user", "content": snippet}],
    )
    text = "".join(
        getattr(b, "text", "") for b in (resp.content or [])
        if getattr(b, "type", "") == "text"
    ).strip()
    usage: Dict[str, Any] = {"model": str(model)}
    try:
        ru = getattr(resp, "usage", None)
        if ru is not None:
            pt = int(getattr(ru, "input_tokens", 0) or 0)
            ct = int(getattr(ru, "output_tokens", 0) or 0)
            if pt or ct:
                usage["prompt_tokens"] = pt
                usage["completion_tokens"] = ct
                usage["total_tokens"] = pt + ct
    except (TypeError, ValueError):
        pass
    return text, _enrich_usage(usage, model)


def summarize_for_speech_with_usage(
    text: str,
    *,
    provider: str = "openai",
    model: str = "",
    target_chars: int,
) -> Tuple[str, Dict[str, Any]]:
    """Ask the narrator model for a short speakable summary.

    Returns (summary_text, usage) where usage carries the step's token
    counts plus a models.dev estimated cost when rates are known.
    """
    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return "", {}
    pid = str(provider or "openai").strip().lower()
    if pid not in SUMMARIZE_PROVIDERS:
        pid = "openai"
    model_id = str(model or "").strip()
    if not model_id:
        from api.completion_providers import resolve_model

        model_id = resolve_model(pid)
    target = max(80, min(2000, int(target_chars or 420)))
    system = _summarize_system(target)
    # Cap prompt size — full agent replies can be huge.
    snippet = cleaned if len(cleaned) <= 12000 else cleaned[:12000] + "…"
    if pid == "anthropic":
        text_out, usage = _summarize_via_anthropic(snippet, system, model_id)
    else:
        text_out, usage = _summarize_via_openai(snippet, system, model_id)
    out = clean_text_for_speech(text_out)
    if len(out) > target * 2:
        out = out[: target * 2].rsplit(" ", 1)[0].strip()
    return (out or cleaned[:target]), usage


def summarize_for_speech(
    text: str,
    *,
    provider: str = "openai",
    model: str = "",
    target_chars: int,
) -> str:
    """Ask the narrator model for a short speakable summary (text only)."""
    spoken, _ = summarize_for_speech_with_usage(
        text, provider=provider, model=model, target_chars=target_chars
    )
    return spoken


def resolve_speech_settings(
    settings: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Effective provider/voice/model/speed/stability for one synthesis."""
    ov = overrides or {}
    provider = str(
        ov.get("provider") or settings.get("provider") or "openai"
    ).strip().lower()
    if provider not in providers.PROVIDER_IDS:
        raise ValueError(f"Unknown TTS provider: {provider}")
    merged_models = dict(settings.get("models") or {})
    if str(ov.get("tts_model") or ov.get("model") or "").strip():
        merged_models[provider] = str(ov.get("tts_model") or ov.get("model")).strip()
    merged_voices = dict(settings.get("voices") or {})
    if str(ov.get("voice") or "").strip():
        merged_voices[provider] = str(ov.get("voice")).strip()
    speed = ov.get("speed", settings.get("speed", 1.0))
    stability = ov.get("stability", settings.get("stability", 0.5))
    return {
        "provider": provider,
        "model": providers.normalize_model(provider, merged_models.get(provider)),
        "voice": providers.normalize_voice(provider, merged_voices.get(provider)),
        "speed": providers.clamp_speed(provider, speed),
        "stability": providers.clamp_stability(provider, stability),
    }


def synthesize_voice(
    text: str,
    *,
    provider: str = "openai",
    voice: Optional[str] = None,
    model: Optional[str] = None,
    speed: Any = None,
    stability: Any = None,
) -> Tuple[bytes, str, Dict[str, Any]]:
    """Synthesize with the given provider; returns (audio, content type, extra)."""
    spoken = clean_text_for_speech(text)
    if not spoken:
        raise ValueError("Nothing to speak")
    pid = str(provider or "openai").strip().lower()
    return providers.synthesize(
        pid, spoken, voice=voice, model=model, speed=speed, stability=stability
    )


def synthesize_speech(
    text: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    voice: Optional[str] = None,
    speed: Any = None,
    stability: Any = None,
) -> bytes:
    """Synthesize with settings defaults (voice narrator entry point)."""
    settings = load_chat_tts_settings()
    resolved = resolve_speech_settings(
        settings,
        {"provider": provider, "tts_model": model, "voice": voice,
         "speed": speed, "stability": stability},
    )
    audio, _, _ = synthesize_voice(
        text,
        provider=resolved["provider"],
        voice=resolved["voice"],
        model=resolved["model"],
        speed=resolved["speed"],
        stability=resolved["stability"],
    )
    return audio


def _prepare_narrator(
    settings: Dict[str, Any],
    overrides: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    ov = overrides or {}
    provider = str(
        ov.get("summarize_provider")
        or settings.get("summarize_provider")
        or "openai"
    ).strip().lower()
    if provider not in SUMMARIZE_PROVIDERS:
        raise ValueError(f"Unknown narrator provider: {provider}")
    explicit = str(ov.get("summarize_model") or "").strip()
    if explicit:
        return provider, explicit
    return provider, effective_summarize_model(settings, provider)


def prepare_spoken_text(
    text: str,
    settings: Dict[str, Any],
    *,
    force_summarize: Optional[bool] = None,
    summarize_provider: Optional[str] = None,
    summarize_model: Optional[str] = None,
) -> Tuple[str, bool]:
    """Return (spoken_text, did_summarize)."""
    spoken, did, _ = prepare_spoken_text_with_usage(
        text, settings,
        force_summarize=force_summarize,
        summarize_provider=summarize_provider,
        summarize_model=summarize_model,
    )
    return spoken, did


def prepare_spoken_text_with_usage(
    text: str,
    settings: Dict[str, Any],
    *,
    force_summarize: Optional[bool] = None,
    summarize_provider: Optional[str] = None,
    summarize_model: Optional[str] = None,
) -> Tuple[str, bool, Dict[str, Any]]:
    """Return (spoken_text, did_summarize, summarize_usage).

    A narrator failure degrades to the full cleaned text instead of
    failing the turn — silence is worse than a long reply.
    """
    cleaned = clean_text_for_speech(text)
    max_in = int(settings.get("max_input_chars") or 24000)
    if len(cleaned) > max_in:
        cleaned = cleaned[:max_in] + "…"

    do_sum = settings.get("summarize", True) if force_summarize is None else bool(force_summarize)
    skip_under = int(settings.get("skip_summarize_under_chars") or 380)
    if not do_sum or len(cleaned) <= skip_under:
        return cleaned, False, {}

    provider, model = _prepare_narrator(
        settings,
        {"summarize_provider": summarize_provider, "summarize_model": summarize_model},
    )
    target = int(settings.get("target_spoken_chars") or 420)
    try:
        spoken, usage = summarize_for_speech_with_usage(
            cleaned, provider=provider, model=model, target_chars=target
        )
    except Exception as exc:
        print(f"[CHAT_TTS] narrator ({provider}/{model}) failed, speaking full text: {exc}")
        return cleaned, False, {}
    return spoken, True, usage


def voice_layer_usage(
    summarize_usage: Dict[str, Any],
    *,
    provider: str,
    tts_model: str,
    tts_chars: int,
    tts_input_tokens: int = 0,
    tts_output_tokens: int = 0,
) -> Dict[str, Any]:
    """Combine the summarize-LLM usage with the TTS audio cost.

    Token counts come from the summarize step; the TTS step's estimate
    folds into ``cost`` (list-price estimate, or computed from Google's
    real token usage). Shape matches chat bubble usage so the same
    footer renderer can display it.
    """
    su = dict(summarize_usage or {})
    try:
        pt = int(su.get("prompt_tokens") or 0)
    except (TypeError, ValueError):
        pt = 0
    try:
        ct = int(su.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        ct = 0
    total = pt + ct
    try:
        sum_cost = float(su.get("cost") or 0)
    except (TypeError, ValueError):
        sum_cost = 0.0
    tts_cost = estimate_tts_cost_usd(
        provider, tts_model, tts_chars,
        input_tokens=tts_input_tokens, output_tokens=tts_output_tokens,
    ) or 0.0
    cost = sum_cost + tts_cost
    out: Dict[str, Any] = {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": total,
        "model": str(su.get("model") or ""),
        "tts_provider": str(provider or ""),
        "tts_chars": int(tts_chars or 0),
        "tts_model": str(tts_model or ""),
        "tts_cost": round(tts_cost, 6),
    }
    if su.get("cost") is not None:
        out["summarize_cost"] = round(sum_cost, 6)
    if cost > 0:
        out["cost"] = round(cost, 6)
        out["cost_estimated"] = True
    return out


def register_chat_tts_routes(app: Flask) -> None:
    @app.route("/api/settings/chat-tts", methods=["GET"])
    @authenticated_required
    def get_chat_tts_settings_api():
        try:
            return jsonify({"success": True, "chat_tts": load_chat_tts_settings()})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/settings/chat-tts", methods=["POST"])
    @owner_required
    def update_chat_tts_settings_api():
        try:
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"success": False, "error": "Expected JSON object"}), 400
            # Allow nested {chat_tts: {...}} or flat body.
            partial = data.get("chat_tts") if isinstance(data.get("chat_tts"), dict) else data
            saved = save_chat_tts_settings(partial)
            return jsonify({"success": True, "chat_tts": saved})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/tts/providers", methods=["GET"])
    @authenticated_required
    def list_tts_providers_api():
        """Registry snapshot for the Voice tab (models, voices, key state)."""
        try:
            from api.completion_providers import list_providers as list_llm

            llm = {p["id"]: p for p in list_llm() if p["id"] in SUMMARIZE_PROVIDERS}
            suggestions = {
                pid: llm.get(pid, {}).get("suggested_models", []) for pid in SUMMARIZE_PROVIDERS
            }
            return jsonify({
                "success": True,
                "providers": providers.list_providers(),
                "summarize_providers": [
                    {
                        "id": pid,
                        "label": llm.get(pid, {}).get("label", pid.title()),
                        "credential_present": llm.get(pid, {}).get("credential_present", False),
                        "configured": llm.get(pid, {}).get("configured", False),
                        "suggested_models": suggestions[pid],
                        "effective_model": llm.get(pid, {}).get("effective_model", ""),
                    }
                    for pid in SUMMARIZE_PROVIDERS
                ],
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/tts/voices", methods=["GET"])
    @authenticated_required
    def fetch_tts_voices_api():
        """The user's own voice library for providers with a list endpoint."""
        try:
            pid = str(request.args.get("provider") or "").strip().lower()
            if pid not in providers.PROVIDER_IDS:
                return jsonify({"success": False, "error": f"Unknown TTS provider: {pid}"}), 400
            voices = providers.fetch_provider_voices(pid)
            if voices is None:
                return jsonify({
                    "success": True, "provider": pid, "voices": [],
                    "note": "This provider has a fixed voice catalog — see the curated list.",
                })
            return jsonify({"success": True, "provider": pid, "voices": voices})
        except RuntimeError as e:
            return jsonify({"success": False, "error": str(e)}), 503
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/chat/tts", methods=["POST"])
    @authenticated_required
    def chat_tts_speak():
        """Summarize (optional) + synthesize, with a 7-day clip cache.

        Body: {text, provider?, voice?, tts_model?, speed?, stability?,
        summarize?, summarize_provider?, summarize_model?}. Overrides let
        the Voice tab preview an unsaved combination; the chat bubble path
        sends only {text} and follows Settings.
        """
        try:
            from api import voice_cache

            data = request.get_json(silent=True) or {}
            text = data.get("text")
            if text is None or not str(text).strip():
                return jsonify({"success": False, "error": "text is required"}), 400

            settings = load_chat_tts_settings()
            if not settings.get("enabled", True):
                return jsonify({"success": False, "error": "Chat TTS is disabled in Settings"}), 403

            try:
                resolved = resolve_speech_settings(settings, {
                    "provider": data.get("provider"),
                    "tts_model": data.get("tts_model") or data.get("model"),
                    "voice": data.get("voice"),
                    "speed": data.get("speed"),
                    "stability": data.get("stability"),
                })
            except ValueError as e:
                return jsonify({"success": False, "error": str(e)}), 400

            force_sum = data.get("summarize")
            if force_sum is not None:
                force_sum = bool(force_sum)
            sprovider = data.get("summarize_provider")
            if sprovider is not None:
                sprovider = str(sprovider).strip().lower()
                if sprovider not in SUMMARIZE_PROVIDERS:
                    return jsonify(
                        {"success": False, "error": f"Unknown narrator provider: {sprovider}"}
                    ), 400
            smodel = data.get("summarize_model")
            if smodel is not None:
                smodel = str(smodel).strip()

            spoken, did_summarize, sum_usage = prepare_spoken_text_with_usage(
                str(text),
                settings,
                force_summarize=force_sum,
                summarize_provider=sprovider,
                summarize_model=smodel,
            )
            if not spoken:
                return jsonify({"success": False, "error": "Nothing speakable in that message"}), 400

            key = voice_cache.cache_key(
                resolved["provider"], resolved["model"], resolved["voice"],
                resolved["speed"], resolved["stability"], spoken,
            )
            hit = voice_cache.get(key)
            if hit is not None:
                audio, content_type, meta = hit
                cached = True
                extra = {
                    "input_tokens": int(meta.get("tts_input_tokens") or 0),
                    "output_tokens": int(meta.get("tts_output_tokens") or 0),
                }
            else:
                audio, content_type, extra = synthesize_voice(
                    spoken,
                    provider=resolved["provider"],
                    voice=resolved["voice"],
                    model=resolved["model"],
                    speed=resolved["speed"],
                    stability=resolved["stability"],
                )
                cached = False
                voice_cache.put(key, audio, content_type, {
                    "provider": resolved["provider"],
                    "model": resolved["model"],
                    "voice": resolved["voice"],
                    "tts_input_tokens": int(extra.get("input_tokens") or 0),
                    "tts_output_tokens": int(extra.get("output_tokens") or 0),
                })
            return jsonify(
                {
                    "success": True,
                    "audio_base64": base64.b64encode(audio).decode("ascii"),
                    "content_type": content_type,
                    "spoken_text": spoken,
                    "summarized": did_summarize,
                    "cached": cached,
                    "provider": resolved["provider"],
                    "tts_model": resolved["model"],
                    "voice": resolved["voice"],
                    "speed": resolved["speed"],
                    "stability": resolved["stability"],
                    "char_count_in": len(clean_text_for_speech(str(text))),
                    "char_count_spoken": len(spoken),
                    "usage": voice_layer_usage(
                        sum_usage,
                        provider=resolved["provider"],
                        tts_model=resolved["model"],
                        tts_chars=len(spoken),
                        tts_input_tokens=int(extra.get("input_tokens") or 0),
                        tts_output_tokens=int(extra.get("output_tokens") or 0),
                    ),
                }
            )
        except RuntimeError as e:
            return jsonify({"success": False, "error": str(e)}), 503
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
