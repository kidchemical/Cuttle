"""Chat bubble text-to-speech (OpenAI).

On demand: optional cheap-model summary → OpenAI TTS → audio bytes.
Settings live in settings.json under ``chat_tts``.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request

from api.http_authz import authenticated_required, owner_required

# OpenAI speech models we expose in Settings / API overrides.
TTS_MODELS = frozenset({"tts-1", "tts-1-hd", "gpt-4o-mini-tts"})

# Built-in voices shared across OpenAI speech models (subset is fine for older TTS).
TTS_VOICES = frozenset(
    {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    }
)

# Cheap chat models for the speak-summary pass (user can switch in Settings).
SUMMARIZE_MODELS = frozenset(
    {
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o3-mini",
        "o4-mini",
    }
)

DEFAULT_CHAT_TTS: Dict[str, Any] = {
    "enabled": True,
    "tts_model": "gpt-4o-mini-tts",
    "voice": "coral",
    "summarize": True,
    "summarize_model": "gpt-4o-mini",
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


def normalize_chat_tts_settings(raw: Any) -> Dict[str, Any]:
    out = default_chat_tts_settings()
    if not isinstance(raw, dict):
        return out

    if "enabled" in raw:
        out["enabled"] = bool(raw.get("enabled"))
    if "summarize" in raw:
        out["summarize"] = bool(raw.get("summarize"))

    model = str(raw.get("tts_model") or "").strip()
    if model in TTS_MODELS:
        out["tts_model"] = model

    voice = str(raw.get("voice") or "").strip().lower()
    if voice in TTS_VOICES:
        out["voice"] = voice

    smodel = str(raw.get("summarize_model") or "").strip()
    if smodel in SUMMARIZE_MODELS:
        out["summarize_model"] = smodel

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
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai package not installed") from e
    return OpenAI(api_key=key)


def summarize_for_speech(
    text: str,
    *,
    model: str,
    target_chars: int,
) -> str:
    """Ask a cheap chat model for a short speakable summary."""
    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return ""
    client = _openai_client()
    target = max(80, min(2000, int(target_chars or 420)))
    system = (
        "You rewrite assistant chat replies for text-to-speech. "
        "Output only plain spoken sentences a listener can follow. "
        "No markdown, bullets, code, URLs, file paths, or UI chrome. "
        f"Aim for about {target} characters (roughly 60–90 words). "
        "Keep the key points; drop boilerplate and apologies."
    )
    # Cap prompt size — full agent replies can be huge.
    snippet = cleaned if len(cleaned) <= 12000 else cleaned[:12000] + "…"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": snippet},
    ]
    create_kw: Dict[str, Any] = {"model": model, "messages": messages}
    # Reasoning mini models reject temperature / max_tokens.
    if str(model).startswith(("o1", "o3", "o4")):
        create_kw["max_completion_tokens"] = 280
    else:
        create_kw["temperature"] = 0.3
        create_kw["max_tokens"] = 280
    resp = client.chat.completions.create(**create_kw)
    out = (resp.choices[0].message.content or "").strip()
    out = clean_text_for_speech(out)
    if len(out) > target * 2:
        out = out[: target * 2].rsplit(" ", 1)[0].strip()
    return out or cleaned[:target]


def synthesize_speech(
    text: str,
    *,
    model: str,
    voice: str,
) -> bytes:
    """Return MP3 bytes from OpenAI /v1/audio/speech."""
    spoken = clean_text_for_speech(text)
    if not spoken:
        raise ValueError("Nothing to speak")
    # Hard caps from OpenAI docs (chars for tts-1*; tokens≈chars for mini-tts).
    limit = 2000 if model == "gpt-4o-mini-tts" else 4096
    if len(spoken) > limit:
        spoken = spoken[:limit].rsplit(" ", 1)[0].strip() or spoken[:limit]

    client = _openai_client()
    kwargs: Dict[str, Any] = {
        "model": model,
        "voice": voice,
        "input": spoken,
        "response_format": "mp3",
    }
    response = client.audio.speech.create(**kwargs)
    data = getattr(response, "content", None)
    if data is None and hasattr(response, "read"):
        data = response.read()
    if not data:
        raise RuntimeError("TTS returned empty audio")
    return bytes(data)


def prepare_spoken_text(
    text: str,
    settings: Dict[str, Any],
    *,
    force_summarize: Optional[bool] = None,
    summarize_model: Optional[str] = None,
) -> Tuple[str, bool]:
    """Return (spoken_text, did_summarize)."""
    cleaned = clean_text_for_speech(text)
    max_in = int(settings.get("max_input_chars") or 24000)
    if len(cleaned) > max_in:
        cleaned = cleaned[:max_in] + "…"

    do_sum = settings.get("summarize", True) if force_summarize is None else bool(force_summarize)
    skip_under = int(settings.get("skip_summarize_under_chars") or 380)
    if not do_sum or len(cleaned) <= skip_under:
        return cleaned, False

    model = (summarize_model or settings.get("summarize_model") or "gpt-4o-mini").strip()
    if model not in SUMMARIZE_MODELS:
        model = "gpt-4o-mini"
    target = int(settings.get("target_spoken_chars") or 420)
    spoken = summarize_for_speech(cleaned, model=model, target_chars=target)
    return spoken, True


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

    @app.route("/api/chat/tts", methods=["POST"])
    @authenticated_required
    def chat_tts_speak():
        """Summarize (optional) + synthesize. Body: {text, tts_model?, voice?, summarize?}."""
        try:
            data = request.get_json(silent=True) or {}
            text = data.get("text")
            if text is None or not str(text).strip():
                return jsonify({"success": False, "error": "text is required"}), 400

            settings = load_chat_tts_settings()
            if not settings.get("enabled", True):
                return jsonify({"success": False, "error": "Chat TTS is disabled in Settings"}), 403

            tts_model = str(data.get("tts_model") or settings["tts_model"]).strip()
            if tts_model not in TTS_MODELS:
                return jsonify({"success": False, "error": f"Invalid tts_model: {tts_model}"}), 400

            voice = str(data.get("voice") or settings["voice"]).strip().lower()
            if voice not in TTS_VOICES:
                return jsonify({"success": False, "error": f"Invalid voice: {voice}"}), 400

            force_sum = data.get("summarize")
            if force_sum is not None:
                force_sum = bool(force_sum)
            smodel = data.get("summarize_model")
            if smodel is not None:
                smodel = str(smodel).strip()
                if smodel not in SUMMARIZE_MODELS:
                    return jsonify({"success": False, "error": f"Invalid summarize_model: {smodel}"}), 400

            spoken, did_summarize = prepare_spoken_text(
                str(text),
                settings,
                force_summarize=force_sum,
                summarize_model=smodel,
            )
            if not spoken:
                return jsonify({"success": False, "error": "Nothing speakable in that message"}), 400

            audio = synthesize_speech(spoken, model=tts_model, voice=voice)
            return jsonify(
                {
                    "success": True,
                    "audio_base64": base64.b64encode(audio).decode("ascii"),
                    "content_type": "audio/mpeg",
                    "spoken_text": spoken,
                    "summarized": did_summarize,
                    "tts_model": tts_model,
                    "voice": voice,
                    "char_count_in": len(clean_text_for_speech(str(text))),
                    "char_count_spoken": len(spoken),
                }
            )
        except RuntimeError as e:
            return jsonify({"success": False, "error": str(e)}), 503
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
