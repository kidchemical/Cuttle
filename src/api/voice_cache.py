"""Server-side cache for synthesized voice clips (previews + replies).

Keyed by provider, model, voice, tunables, and text hash, so replaying
the same reply (or re-previewing a voice) costs nothing. Entries expire
after 7 days; the directory is capped (~200MB) with oldest-first
eviction. Everything lives under ``runtime_cache_path("voice_tts")`` —
deleting it only causes re-synthesis.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

TTL_SECONDS = 7 * 24 * 3600
MAX_BYTES = 200 * 1024 * 1024


def cache_dir() -> Path:
    from core.runtime_paths import runtime_cache_path

    return runtime_cache_path("voice_tts")


def cache_key(
    provider: str,
    model: str,
    voice: str,
    speed: Any,
    stability: Any,
    text: str,
) -> str:
    h = hashlib.sha256()
    h.update(b"v1\x00")
    for part in (
        str(provider or ""),
        str(model or ""),
        str(voice or ""),
        str(speed if speed is not None else ""),
        str(stability if stability is not None else ""),
        str(text or ""),
    ):
        h.update(part.encode("utf-8") + b"\x00")
    return h.hexdigest()


def _paths(key: str) -> Tuple[Path, Path]:
    d = cache_dir()
    return d / f"{key}.bin", d / f"{key}.json"


def get(key: str) -> Optional[Tuple[bytes, str, Dict[str, Any]]]:
    """Return (audio, content type, meta) on a fresh hit, else None."""
    if not key or "/" in key or "\\" in key:
        return None
    audio_path, meta_path = _paths(key)
    try:
        if not audio_path.exists():
            return None
        if time.time() - audio_path.stat().st_mtime > TTL_SECONDS:
            return None
        meta: Dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        return audio_path.read_bytes(), str(meta.get("content_type") or "audio/mpeg"), meta
    except OSError:
        return None


def put(
    key: str, audio: bytes, content_type: str, meta: Optional[Dict[str, Any]] = None
) -> None:
    """Store a clip; failures are silent (cache must never break speech)."""
    if not key or "/" in key or "\\" in key or not audio:
        return
    try:
        audio_path, meta_path = _paths(key)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(bytes(audio))
        record = dict(meta or {})
        record["content_type"] = content_type
        record["created"] = int(time.time())
        meta_path.write_text(json.dumps(record), encoding="utf-8")
        _enforce_cap(audio_path.parent)
    except OSError:
        pass


def _enforce_cap(directory: Path) -> None:
    try:
        bins = [p for p in directory.glob("*.bin") if p.is_file()]
        total = sum(p.stat().st_size for p in bins)
        if total <= MAX_BYTES:
            return
        bins.sort(key=lambda p: p.stat().st_mtime)
        for old in bins:
            try:
                total -= old.stat().st_size
                old.unlink()
                sidecar = old.with_suffix(".json")
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                continue
            if total <= MAX_BYTES:
                return
    except OSError:
        pass


def stats() -> Dict[str, Any]:
    """Rough cache health for diagnostics (never raises)."""
    try:
        d = cache_dir()
        bins = [p for p in d.glob("*.bin") if p.is_file()]
        return {
            "entries": len(bins),
            "bytes": sum(p.stat().st_size for p in bins),
            "max_bytes": MAX_BYTES,
            "ttl_seconds": TTL_SECONDS,
        }
    except OSError:
        return {"entries": 0, "bytes": 0, "max_bytes": MAX_BYTES, "ttl_seconds": TTL_SECONDS}
