"""Account-scoped model discovery and operation execution over provider adapters."""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from . import cache, capabilities, models_dev
from .catalog import get_adapter, get_provider, manifests
from .types import TextRequest, TextResult, SpeechRequest, SpeechResult, TranscriptionRequest

_LOCKS = {p.id: threading.Lock() for p in manifests()}


def _snapshot_name(manifest, adapter) -> str:
    # Credentials only affect this one-way identity. They never enter snapshots,
    # logs or public output. Changing the account or endpoint invalidates access.
    identity = hashlib.sha256(adapter.discovery_identity(manifest).encode()).hexdigest()[:32]
    return f"model_provider_{manifest.id}_{identity}.json"


def _record(manifest, live, meta_snapshot, availability, operation="text_generation"):
    mid = live["id"]
    meta = models_dev.model_metadata(manifest, mid, meta_snapshot)
    caps = capabilities.resolve(manifest, mid, metadata=meta, live=live)
    operations = caps.get("operations") or []
    compatibility = ("supported" if operation in operations else
                     "unsupported" if "operations" in caps["sources"] else "unknown")
    return {
        "id": mid, "label": live.get("label") or meta.get("name") or mid,
        "availability": availability, "compatibility": compatibility,
        "capabilities": caps, "lifecycle": live.get("lifecycle"),
        "metadata_source": "models_dev" if meta else None,
        "cost": meta.get("cost"), "limit": meta.get("limit"),
    }


def _account_changed(manifest, operation="text_generation"):
    metadata = models_dev.snapshot()
    return {
        "provider": manifest.id,
        "models": [_record(manifest, row, metadata, "unknown", operation) for row in manifest.models],
        "source": "manifest_fallback", "fetched_at": None, "stale": True,
        "message": "Provider credentials or endpoint changed. Refresh the model list.",
        "metadata_error": metadata.get("error"), "metadata_stale": metadata.get("stale", True),
    }


def discover_models(provider_id: str, *, refresh: bool = False, operation: str = "text_generation") -> dict[str, Any]:
    manifest = get_provider(provider_id)
    if manifest is None:
        raise ValueError("Unknown model provider")
    if operation not in manifest.operations:
        raise ValueError("Unsupported provider operation")
    adapter = get_adapter(manifest.id)
    with _LOCKS[manifest.id]:
        name = _snapshot_name(manifest, adapter)
        saved = cache.read(name)
        ttl = 60 if not manifest.needs_credential else 3600
        now = time.time()
        expired = now - saved.get("fetched_at", 0) >= ttl
        if manifest.configured() and (refresh or (expired and now - saved.get("checked_at", 0) >= 60)):
            try:
                rows = adapter.list_models(manifest)
                if _snapshot_name(manifest, adapter) != name:
                    return _account_changed(manifest, operation)
                if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"] for r in rows):
                    raise ValueError("Invalid provider model list")
                # Persist only the adapter's normalized metadata, not SDK bodies.
                rows = list({r["id"]: r for r in rows}.values())
                saved = {"models": rows, "fetched_at": time.time(), "checked_at": time.time(), "error": None}
                expired = False
            except Exception as exc:
                if _snapshot_name(manifest, adapter) != name:
                    return _account_changed(manifest, operation)
                saved = {**saved, "checked_at": time.time(),
                         "error": cache.public_error(exc, "Provider model list")}
            try:
                cache.write(name, saved)
            except OSError:
                pass

        has_snapshot = "models" in saved  # A successful empty list stays empty.
        raw = saved.get("models", []) if has_snapshot else list(manifest.models)
        metadata = models_dev.snapshot(refresh=refresh and has_snapshot,
                                       fetch=has_snapshot and manifest.configured())
        if _snapshot_name(manifest, adapter) != name:
            return _account_changed(manifest, operation)
        availability = "available" if has_snapshot and not expired else "unknown"
        rows = [_record(manifest, row, metadata, availability, operation) for row in raw]
        source = "provider_api" if has_snapshot else "manifest_fallback"
        if has_snapshot and expired:
            source = "stale_provider_api"
        message = saved.get("error")
        if not manifest.configured():
            message = "Add this provider's API key to list models available to your account."
        elif not has_snapshot and not message:
            message = "Showing fallback suggestions; account access has not been checked."
        if message and "HTTP 401" in message:
            message += " This is API-key authentication, separate from CLI login. Replace the saved provider API key in Settings → Providers → API Keys."
        return {
            "operation": operation, "provider": manifest.id, "models": rows, "source": source,
            "fetched_at": saved.get("fetched_at"), "stale": expired,
            "message": message, "metadata_error": metadata.get("error"),
            "metadata_stale": metadata.get("stale", True),
        }


def cached_capabilities(provider_id: str, model_id: str) -> dict[str, Any]:
    """No remote reads: a completion must never wait for catalog discovery."""
    manifest = get_provider(provider_id)
    if manifest is None:
        raise ValueError("Unknown model provider")
    saved = cache.read(_snapshot_name(manifest, get_adapter(manifest.id)))
    live = next((r for r in saved.get("models", []) if r["id"] == model_id), {})
    meta = models_dev.model_metadata(manifest, model_id, models_dev.snapshot())
    return capabilities.resolve(manifest, model_id, metadata=meta, live=live)


def complete_text(provider_id: str, request: TextRequest) -> str | None:
    """Text-only facade over the canonical operation/result contract."""
    result = generate_text(provider_id, request)
    return result.text if result else None


def validate_effort(provider_id, model, effort):
    if not effort:
        return None
    caps = cached_capabilities(provider_id, model)
    if effort not in caps.get("reasoning_efforts", []):
        raise ValueError("This model does not advertise the selected effort. Refresh models and choose a supported effort.")
    return effort


def validate_temperature(provider_id, model, temperature, effort=None):
    import math
    control = capabilities.temperature_control(cached_capabilities(provider_id, model), effort)
    if not control["supported"]: raise ValueError(control["reason"])
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
        raise ValueError("Temperature must be a finite number")
    if not control["min"] <= temperature <= control["max"]:
        raise ValueError(f'Temperature must be between {control["min"]} and {control["max"]}')
    return temperature


def generate_text(provider_id: str, request: TextRequest) -> TextResult | None:
    manifest = get_provider(provider_id)
    if manifest is None or not manifest.configured() or "text_generation" not in manifest.operations:
        return None
    caps = cached_capabilities(provider_id, request.model)
    if "operations" in caps["sources"] and "text_generation" not in caps["operations"]:
        return None
    if request.json_object and caps.get("json_object") is False:
        return None
    validate_effort(provider_id, request.model, request.effort)
    if request.images and caps.get("input_modalities") and "image" not in caps["input_modalities"]:
        raise ValueError("This model does not support image input")
    return get_adapter(provider_id).generate_text(manifest, request, caps)


def _operation(provider_id, operation, model):
    manifest = get_provider(provider_id)
    if manifest is None or operation not in manifest.operations:
        raise ValueError("Unsupported model provider operation")
    if not manifest.configured():
        raise RuntimeError("Provider API key is not set (" + " / ".join(manifest.credential_env) + ")")
    caps = cached_capabilities(provider_id, model)
    if "operations" in caps["sources"] and operation not in caps["operations"]:
        raise ValueError("Model does not support this operation")
    return manifest, get_adapter(provider_id), caps


def synthesize_speech(provider_id: str, request: SpeechRequest) -> SpeechResult:
    manifest, adapter, caps = _operation(provider_id, "speech_generation", request.model)
    # Built-in model voice restrictions are checked without remote catalog reads.
    if not manifest.speech.get("fetch_voices"):
        voices = adapter.list_voices(manifest, request.model)
        if voices and request.voice not in {v["id"] for v in voices}:
            raise ValueError("Voice is not supported by the selected model")
    return adapter.synthesize_speech(manifest, request, caps)


def transcribe(provider_id: str, request: TranscriptionRequest) -> str:
    manifest, adapter, _ = _operation(provider_id, "audio_transcription", request.model)
    return adapter.transcribe(manifest, request)


def check_key(provider_id: str, key: str):
    manifest = get_provider(provider_id)
    if manifest is None or not key.strip():
        return False, "Provider API key is required"
    try:
        get_adapter(provider_id).check_key(manifest, key.strip())
        return True, f"Valid {manifest.label} API key (authenticated read; no content generated)"
    except Exception as exc:
        return False, cache.public_error(exc, f"{manifest.label} API key test")


def discover_voices(provider_id: str, *, model: str = "", refresh: bool = False):
    manifest = get_provider(provider_id)
    if manifest is None or "speech_generation" not in manifest.operations:
        raise ValueError("Unknown speech provider")
    adapter = get_adapter(provider_id)
    if not manifest.speech.get("fetch_voices"):
        return dict(provider=provider_id, voices=adapter.list_voices(manifest, model), source="provider_manifest", message=None)
    with _LOCKS[provider_id]:
        identity = _snapshot_name(manifest, adapter)
        suffix = hashlib.sha256(model.encode()).hexdigest()[:16]
        name = identity.removesuffix(".json") + "_voices_" + suffix + ".json"
        saved = cache.read(name)
        now = time.time()
        if manifest.configured() and (refresh or now - saved.get("fetched_at", 0) >= 3600) and (refresh or now - saved.get("checked_at", 0) >= 60):
            try:
                rows = adapter.list_voices(manifest, model)
                if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"] for r in rows):
                    raise ValueError("Invalid provider voice list")
                if _snapshot_name(manifest, adapter) != identity:
                    raise ValueError("Provider account changed; refresh voices")
                # Cache uses a normalized 'models' list for schema validation.
                saved = dict(models=rows, fetched_at=time.time(), checked_at=time.time(), error=None)
            except Exception as exc:
                if _snapshot_name(manifest, adapter) != identity:
                    return dict(provider=provider_id, voices=[], source="manifest_fallback", message="Provider account changed; refresh voices")
                saved = {**saved, "checked_at": time.time(), "error": cache.public_error(exc, "Voice discovery")}
            try:
                cache.write(name, saved)
            except OSError:
                pass
        if _snapshot_name(manifest, adapter) != identity:
            return dict(provider=provider_id, voices=[], source="manifest_fallback", message="Provider account changed; refresh voices")
        rows = saved["models"] if "models" in saved else manifest.speech.get("voices", [])
        return dict(provider=provider_id, voices=rows,
                    source=("provider_api" if now - saved.get("fetched_at", 0) < 3600 else "stale_provider_api") if "models" in saved else "manifest_fallback",
                    message=saved.get("error") or (None if manifest.configured() else "Add a provider API key to list account voices."))


def decide(provider_id, request):
    manifest = get_provider(provider_id)
    if manifest is None or 'structured_decision' not in manifest.operations:
        raise ValueError('Unsupported decision provider')
    return get_adapter(provider_id).decide(manifest, request)
