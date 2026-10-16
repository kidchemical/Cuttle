"""Provider registry + discovery — the pluggable seam.

Discovery order (first definition of an id wins):
  1. bundled   ``src/api/knowledge_graph/providers/<id>/``
  2. user dir  ``<home>/knowledge_graph/providers/<id>/``
  3. env dirs  ``CUTTLE_KG_PROVIDERS_DIR`` (``os.pathsep``-separated)

Each candidate must have a ``manifest.yaml`` whose ``id`` matches its folder and
an ``adapter.py`` exposing ``build_provider()``. Validation happens before any
provider code runs. Nothing runs at import time except reading manifests.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import cuttle_home

from api.knowledge_graph.manifest import ProviderInfo, load_manifest
from api.knowledge_graph.protocol import KnowledgeGraphProvider

BUNDLED_DIR = Path(__file__).resolve().parent / "providers"

_cache: Optional[Dict[str, Any]] = None


def user_providers_dir() -> Path:
    return cuttle_home() / "knowledge_graph" / "providers"


def _provider_dirs() -> List[tuple]:
    dirs = [(BUNDLED_DIR, "bundled"), (user_providers_dir(), "user")]
    env = (os.environ.get("CUTTLE_KG_PROVIDERS_DIR") or "").strip()
    if env:
        for part in env.split(os.pathsep):
            part = part.strip()
            if part:
                dirs.append((Path(part).expanduser(), "env"))
    return dirs


def _discover_manifests() -> Dict[str, ProviderInfo]:
    found: Dict[str, ProviderInfo] = {}
    for base, source in _provider_dirs():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            manifest = child / "manifest.yaml"
            if not (child.is_dir() and manifest.is_file()):
                continue
            try:
                info = load_manifest(manifest, source=source, expect_id=child.name)
            except ValueError:
                continue
            if info.id not in found:
                found[info.id] = info
    return found


def _load_adapter(info: ProviderInfo) -> KnowledgeGraphProvider:
    if info.source == "bundled":
        module = importlib.import_module(
            f"api.knowledge_graph.providers.{info.id}.adapter"
        )
    else:
        adapter_path = info.path.parent / "adapter.py"
        if not adapter_path.is_file():
            raise ValueError(f"provider {info.id!r} has no adapter.py")
        spec = importlib.util.spec_from_file_location(
            f"cuttle_kg_provider_{info.id}", adapter_path
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load adapter for provider {info.id!r}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    factory = getattr(module, "build_provider", None)
    provider = factory() if callable(factory) else factory
    if provider is None:
        raise ValueError(f"provider {info.id!r} has no build_provider()")
    for attr in ("id", "label", "available", "index", "query"):
        if not hasattr(provider, attr):
            raise ValueError(f"provider {info.id!r} violates the contract (missing {attr})")
    return provider


def _registry() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        providers: Dict[str, Any] = {}
        infos = _discover_manifests()
        for pid, info in infos.items():
            try:
                providers[pid] = {"info": info, "provider": _load_adapter(info)}
            except (ValueError, ImportError, AttributeError):
                continue
        _cache = providers
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def list_provider_infos() -> List[ProviderInfo]:
    return [row["info"] for row in _registry().values()]


def default_provider_id() -> str:
    env = (os.environ.get("CUTTLE_KG_PROVIDER") or "").strip()
    if env:
        return env
    try:
        from managers.settings_manager import get_settings_manager

        settings = get_settings_manager().get_settings() or {}
        configured = ((settings.get("knowledge_graph") or {}).get("provider") or "").strip()
        if configured:
            return configured
    except Exception:
        pass
    return "graphify"


def get_provider(provider_id: Optional[str] = None) -> Optional[KnowledgeGraphProvider]:
    registry = _registry()
    pid = provider_id or default_provider_id()
    row = registry.get(pid)
    return row["provider"] if row else None


def get_provider_info(provider_id: Optional[str] = None) -> Optional[ProviderInfo]:
    registry = _registry()
    pid = provider_id or default_provider_id()
    row = registry.get(pid)
    return row["info"] if row else None


def provider_status() -> List[Dict[str, Any]]:
    registry = _registry()
    default = default_provider_id()
    rows = []
    for pid, row in registry.items():
        provider = row["provider"]
        try:
            available = bool(provider.available())
        except Exception:
            available = False
        rows.append({
            "id": pid, "label": getattr(provider, "label", pid),
            "available": available, "default": pid == default,
            "source": row["info"].source,
            "capabilities": list(row["info"].capabilities),
            "store_root": row["info"].store_root,
            "install_hint": row["info"].install_hint,
        })
    return rows
