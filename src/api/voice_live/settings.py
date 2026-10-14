"""Validated conversation-engine preferences, separate from rollout flags."""
from api.experimental import is_enabled
from api.model_providers.catalog import get_provider
from api.model_providers.openai.live import MODEL, VOICES

DEFAULTS = {"engine": "auto", "model": MODEL, "voice": "marin", "idle_seconds": 150, "speed": 1.0, "progress_seconds": 0}

# Provider-accepted speaking-speed bounds (OpenAI Realtime `audio.output.speed`).
SPEED_RANGE = (0.25, 1.5)
# Minimum seconds between spoken progress lines while a delegated turn works
# (0 = speak every backend status update).
PROGRESS_RANGE = (0, 300)
# Live voices that OpenAI text-to-speech also speaks. Settings previews use TTS
# (a Live session is billed while connected), so only these can be previewed.
PREVIEW_VOICES = frozenset("alloy ash ballad cedar coral echo marin sage shimmer verse".split())


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
    speed = out["speed"]
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not (SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]):
        raise ValueError("Speaking speed must be between 0.25× and 1.5×.")
    out["speed"] = round(float(speed), 2)
    if isinstance(out["progress_seconds"], bool) or not isinstance(out["progress_seconds"], int) or not (PROGRESS_RANGE[0] <= out["progress_seconds"] <= PROGRESS_RANGE[1]):
        raise ValueError("Spoken progress interval must be 0 (every update) or at most 300 seconds.")
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
    # A saved engine whose flag is now off falls back to another *enabled*
    # engine, not straight to plain TTS. Otherwise switching from GPT-Live to
    # Realtime in Experimental (without re-picking the engine) left the saved
    # "live" preference resolving to "tts" and Realtime was never engaged.
    if active == "live" and not live:
        active = "realtime" if realtime else "tts"
    elif active == "realtime" and not realtime:
        active = "live" if live else "tts"
    return {"voice_chat": prefs, "engine": active, "live_enabled": live,
            "realtime_enabled": realtime, "configured": bool(provider and provider.configured()),
            "models": [{"id": MODEL, "label": "GPT-Live 1"}],
            "speed_range": list(SPEED_RANGE),
            "progress_range": list(PROGRESS_RANGE),
            "voices": [{"id": voice, "label": voice, "preview": voice in PREVIEW_VOICES} for voice in VOICES]}
