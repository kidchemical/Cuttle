"""One shared models.dev transport and raw metadata cache.

Pricing derives its index from this same feed. Account access never comes from
models.dev. A failed refresh preserves the last successful metadata snapshot.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any

from . import cache

URL = "https://models.dev/api.json"
NAME = "model_providers_models_dev.json"
TTL = 24 * 60 * 60
LOCK = threading.Lock()


def fetch_catalog() -> dict[str, Any]:
    req = urllib.request.Request(URL, headers={"User-Agent": "Cuttle-ModelProviders/1.0",
                                              "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("models.dev response too large")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or not data:
        raise ValueError("Invalid models.dev catalog")
    return data


def snapshot(*, refresh: bool = False, fetch: bool = False) -> dict[str, Any]:
    with LOCK:
        saved = cache.read(NAME)
        stale = time.time() - saved.get("fetched_at", 0) >= TTL
        if refresh or (fetch and stale and time.time() - saved.get("checked_at", 0) > 60):
            try:
                saved = {"catalog": fetch_catalog(), "fetched_at": time.time(),
                         "checked_at": time.time(), "error": None}
                stale = False
            except Exception as exc:
                saved = {**saved, "checked_at": time.time(),
                         "error": cache.public_error(exc, "Model capability metadata")}
            try:
                cache.write(NAME, saved)
            except OSError:
                pass  # A read-only/full cache must not block provider discovery.
        return {**saved, "stale": stale}


def model_metadata(manifest, model_id: str, snapshot_data: dict[str, Any]) -> dict[str, Any]:
    provider = (snapshot_data.get("catalog") or {}).get(manifest.models_dev_provider) or {}
    if not isinstance(provider, dict):
        return {}
    models = provider.get("models") or {}
    if not isinstance(models, dict):
        return {}
    # Provider-specific IDs only: no reseller/leaf/fuzzy joins for capabilities.
    row = models.get(model_id) or {}
    return row if isinstance(row, dict) else {}
