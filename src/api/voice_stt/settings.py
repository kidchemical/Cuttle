"""Speech-to-text preferences (settings key ``voice_stt``).

One choice serves every surface that turns recorded speech into text: composer
voice clips and voice mode's server transcription. The provider is a
``api.model_providers`` entry that declares ``audio_transcription`` (OpenAI, a
user-managed local server, ElevenLabs), or ``browser``: the browser's own
speech recognizer, which runs on the client and never reaches this server.
"""
from __future__ import annotations

import re

from api.model_providers.catalog import get_provider, manifests

BROWSER = "browser"
OPERATION = "audio_transcription"
CLIP_MINUTES_RANGE = (1, 30)
DEFAULTS = {"provider": "openai", "models": {}, "local_base_url": "", "live_text": True,
            "max_clip_minutes": 10}


def providers():
    return [p for p in manifests() if OPERATION in p.operations]


def normalize(raw):
    out = {**DEFAULTS, "models": {}}
    if isinstance(raw, dict):
        out.update({key: raw[key] for key in DEFAULTS if key in raw})
    ids = {p.id for p in providers()}
    if out["provider"] not in ids | {BROWSER}:
        raise ValueError("Choose a supported speech-to-text provider.")
    models = out["models"]
    if not isinstance(models, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in models.items()):
        raise ValueError("Speech-to-text models must be text.")
    out["models"] = {k: v.strip()[:200] for k, v in models.items() if k in ids and v.strip()}
    url = out["local_base_url"]
    if not isinstance(url, str) or (url.strip() and not re.fullmatch(r"https?://[^\s]+", url.strip())):
        raise ValueError("The local server address must start with http:// or https://.")
    out["local_base_url"] = url.strip().rstrip("/")
    if not isinstance(out["live_text"], bool):
        raise ValueError("Live text must be on or off.")
    minutes = out["max_clip_minutes"]
    low, high = CLIP_MINUTES_RANGE
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not low <= minutes <= high:
        raise ValueError(f"Longest voice clip must be {low} to {high} minutes.")
    return out


def load():
    from managers.settings_manager import get_settings_manager
    try:
        return normalize(get_settings_manager().get_setting("voice_stt"))
    except ValueError:
        return normalize(None)


def save(data):
    from managers.settings_manager import get_settings_manager
    if not isinstance(data, dict):
        raise ValueError("Expected speech-to-text settings.")
    out = normalize({**load(), **data})
    if not get_settings_manager().set_setting("voice_stt", out):
        raise RuntimeError("Could not save speech-to-text settings.")
    return out


def resolve(prefs=None):
    """``(provider_id, [models to try], base_url)``; provider is ``browser`` or a manifest id."""
    prefs = prefs or load()
    pid = prefs["provider"]
    manifest = get_provider(pid)
    if pid == BROWSER or manifest is None:
        return BROWSER, [], ""
    chosen = prefs["models"].get(pid, "")
    declared = manifest.transcription
    chain = [chosen] if chosen else [m for m in (declared.get("default_model"), declared.get("fallback_model")) if m]
    return pid, chain, prefs["local_base_url"] if pid == "local" else ""


def configured(prefs=None) -> bool:
    """True when the server can transcribe (never for the browser recognizer)."""
    pid, _, _ = resolve(prefs)
    manifest = get_provider(pid)
    return bool(manifest and manifest.configured())


def clip_status():
    """What the composer mic needs before it records."""
    prefs = load()
    server = configured(prefs)
    engine = "none"
    if prefs["live_text"]:
        engine = BROWSER if prefs["provider"] == BROWSER else "server" if server else "none"
    return {"provider": prefs["provider"], "engine": engine, "configured": server,
            "live_text": prefs["live_text"], "max_clip_ms": prefs["max_clip_minutes"] * 60_000}


def catalog():
    prefs = load()
    rows = []
    for p in providers():
        suggested = [{"id": m["id"], "label": m.get("label") or m["id"]} for m in p.models
                     if OPERATION in (m.get("capabilities") or {}).get("operations", [])]
        rows.append({"id": p.id, "label": p.label, "configured": p.configured(), "models": suggested,
                     "default_model": p.transcription.get("default_model", ""),
                     "needs_credential": p.needs_credential, "local": p.id == "local"})
    rows.append({"id": BROWSER, "label": "Browser speech recognition (no API key)", "configured": True,
                 "models": [], "default_model": "", "needs_credential": False, "local": False, "browser": True})
    return {"voice_stt": prefs, "providers": rows, "clip_minutes_range": list(CLIP_MINUTES_RANGE),
            "status": clip_status()}
