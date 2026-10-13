"""Validated conversation-engine preferences, separate from rollout flags."""
from api.experimental import is_enabled
from api.model_providers.catalog import get_provider
from api.model_providers.openai.live import MODEL, VOICES

DEFAULTS = {"engine": "auto", "model": MODEL, "voice": "marin", "idle_seconds": 150}


def normalize(raw):
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out.update({key: raw[key] for key in DEFAULTS if key in raw})
    if out["engine"] not in {"auto", "tts", "live", "realtime"}:
        raise ValueError("Choose a supported conversation engine.")
    if out["model"] != MODEL:
        raise ValueError("GPT-Live currently supports gpt-live-1.")
    if out["voice"] not in VOICES:
        raise ValueError("Choose a supported GPT-Live voice.")
    if isinstance(out["idle_seconds"], bool) or not isinstance(out["idle_seconds"], int) or not (0 <= out["idle_seconds"] <= 3600):
        raise ValueError("Idle timeout must be 0 (continuous) or at most 3600 seconds.")
    return out


def load():
    from managers.settings_manager import get_settings_manager
    try:
        return normalize(get_settings_manager().get_setting("voice_chat"))
    except ValueError:
        return dict(DEFAULTS)


def save(data):
    from managers.settings_manager import get_settings_manager
    out = normalize({**load(), **data})
    engine = out["engine"]
    if engine in {"live", "realtime"} and not is_enabled("voice_" + engine):
        raise ValueError("Enable the selected voice engine in Settings → Experimental first.")
    if not get_settings_manager().set_setting("voice_chat", out):
        raise RuntimeError("Could not save voice conversation settings.")
    return out


def catalog():
    provider = get_provider("openai")
    prefs = load()
    active = prefs["engine"]
    live, realtime = is_enabled("voice_live"), is_enabled("voice_realtime")
    if active == "auto":
        active = "live" if live else "realtime" if realtime else "tts"
    if active == "live" and not live or active == "realtime" and not realtime:
        active = "tts"
    return {"voice_chat": prefs, "engine": active, "live_enabled": live,
            "realtime_enabled": realtime, "configured": bool(provider and provider.configured()),
            "models": [{"id": MODEL, "label": "GPT-Live 1"}],
            "voices": [{"id": voice, "label": voice} for voice in VOICES]}
