"""Which provider and model serve Cuttle's own cheap completions.

Chat titles, commit subjects, prompt enhance, and the router brain all funnel
through :func:`api.llm_complete.complete`. Until now that function hardcoded
``gpt-4o-mini`` / ``claude-haiku-4-5`` and read an env var no UI ever wrote, so
the two "cheap completion model" dropdowns in Settings were dead controls:
they POSTed ``preferred_llm_model`` / ``preferred_tools_ollama_model`` and
nothing consulted either one.

Provider declarations come from ``api.model_providers`` manifests. This module
owns background-helper preferences (``completion_provider`` and
``completion_models``), fallback order, and model resolution:
explicit argument → environment → Settings → provider default.

Precedence note: this registry resolves *which provider/model*. Credential
availability still comes from the environment, and providers with no
credential simply report themselves unavailable instead of raising.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from api.model_providers.catalog import manifests
from api.model_providers.types import ProviderManifest as CompletionProvider

SETTINGS_KEY = 'completion_provider'
MODELS_KEY = 'completion_models'
EFFORTS_KEY = 'completion_efforts'
_REGISTRY = tuple(p for p in manifests() if 'text_generation' in p.operations)
_BY_ID = {p.id: p for p in _REGISTRY}
DEFAULT_ORDER: Tuple[str, ...] = tuple(p.id for p in _REGISTRY)


def get_provider(provider_id: Optional[str]) -> Optional[CompletionProvider]:
    """Only providers implementing background text belong in this facade."""
    return _BY_ID.get(str(provider_id or '').strip().lower())


def list_providers() -> List[Dict[str, Any]]:
    """Public shape for the Settings UI."""
    return [_public_provider(p) for p in _REGISTRY]


def _settings() -> Optional[Any]:
    try:
        from managers.settings_manager import get_settings_manager

        return get_settings_manager()
    except Exception:
        return None


def preferred_provider_id() -> Optional[str]:
    """Provider the user pinned in Settings, or None when unpinned.

    ``None`` is a real, useful answer: it means "try every configured provider
    in registry order", which is what a fresh install should do.
    """
    settings = _settings()
    if settings is None:
        return None
    try:
        raw = settings.get_setting(SETTINGS_KEY) or {}
    except Exception:
        return None
    pinned = str((raw or {}).get('provider') or '').strip().lower()
    return pinned if pinned in _BY_ID else None


def set_preferred_provider(provider_id: Optional[str]) -> bool:
    """Pin a provider, or clear the pin with None/''/'auto'.

    The model map is preserved when switching providers so a user's typed
    model id is not lost when they hop back.
    """
    normalized = str(provider_id or '').strip().lower()
    if normalized in ('', 'auto', 'none'):
        normalized = ''
    elif normalized not in _BY_ID:
        return False
    settings = _settings()
    if settings is None:
        return False
    try:
        current = dict(settings.get_setting(SETTINGS_KEY) or {})
    except Exception:
        current = {}
    current['provider'] = normalized
    try:
        return bool(settings.set_setting(SETTINGS_KEY, current))
    except Exception:
        return False


def _model_map() -> Dict[str, str]:
    settings = _settings()
    if settings is None:
        return {}
    try:
        raw = settings.get_setting(MODELS_KEY) or {}
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(v or '').strip()}


def set_provider_model(provider_id: str, model: Optional[str]) -> bool:
    """Store (or clear, with None/'') the model id for one provider."""
    key = str(provider_id or '').strip().lower()
    if key not in _BY_ID:
        return False
    settings = _settings()
    if settings is None:
        return False
    try:
        current = dict(settings.get_setting(MODELS_KEY) or {})
    except Exception:
        current = {}
    value = str(model or '').strip()
    if value:
        current[key] = value
    else:
        current.pop(key, None)
    try:
        return bool(settings.set_setting(MODELS_KEY, current))
    except Exception:
        return False


def resolve_model(provider_id: str, explicit: Optional[str] = None) -> str:
    """Model id for one provider: explicit → env → Settings → provider default.

    Local resolves to '' when nothing is configured, because
    ``core.local_llm.resolve_local_model`` already owns that decision (it knows
    about llama.cpp vs Ollama and the loaded model).
    """
    provider = get_provider(provider_id)
    if provider is None:
        return ''
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    if provider.model_env:
        from_env = (os.getenv(provider.model_env) or '').strip()
        if from_env:
            return from_env
    from_settings = _model_map().get(provider.id, '').strip()
    if from_settings:
        return from_settings
    return provider.default_model


def resolve_order(explicit: Optional[str] = None) -> List[str]:
    """Providers to try, in order, for one cheap completion.

    An explicit provider pins the chain to just that provider (so a caller
    asking for ``local`` never silently burns an OpenAI key), otherwise the
    user's pin goes first and the rest follow in registry order.
    """
    requested = str(explicit or '').strip().lower()
    if requested:
        return [requested] if requested in _BY_ID else []
    pinned = preferred_provider_id()
    if pinned:
        return [pinned] + [p for p in DEFAULT_ORDER if p != pinned]
    return list(DEFAULT_ORDER)


def describe() -> Dict[str, Any]:
    """Everything a settings page needs, in one payload."""
    pinned = preferred_provider_id()
    models = _model_map()
    providers = []
    for provider in _REGISTRY:
        row = _public_provider(provider)
        row['effective_model'] = resolve_model(provider.id)
        row['saved_model'] = models.get(provider.id, '')
        row['model_from'] = (
            'env' if provider.model_env and (os.getenv(provider.model_env) or '').strip()
            else 'settings' if provider.id in models
            else 'default'
        )
        row['pinned'] = provider.id == pinned
        providers.append(row)
    return {
        'settings_key': SETTINGS_KEY,
        'models_key': MODELS_KEY,
        'pinned_provider': pinned or '',
        'order': resolve_order(),
        'providers': providers,
        'efforts': effort_map(),
    }


def effort_map():
    settings = _settings()
    try:
        raw = settings.get_setting(EFFORTS_KEY) if settings else {}
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {pid: {mid: e for mid, e in rows.items() if isinstance(mid, str) and isinstance(e, str)}
            for pid, rows in raw.items() if pid in _BY_ID and isinstance(rows, dict)}


def resolve_effort(provider_id, model, explicit=None):
    return explicit if explicit is not None else effort_map().get(provider_id, {}).get(model) or None


def validate_efforts(raw):
    from api.model_providers.service import validate_effort
    if not isinstance(raw, dict):
        raise ValueError("Efforts must be a provider/model map")
    for pid, rows in raw.items():
        if pid not in _BY_ID or not isinstance(rows, dict):
            raise ValueError("Invalid effort provider")
        for mid, effort in rows.items():
            if not isinstance(mid, str) or not mid.strip() or not isinstance(effort, str):
                raise ValueError("Invalid effort choice")
            validate_effort(pid, mid, effort)
    return raw


def set_efforts(raw):
    raw = validate_efforts(raw)
    settings = _settings()
    return bool(settings and settings.set_setting(EFFORTS_KEY, raw))


def _public_provider(provider):
    from api.model_providers.capabilities import resolve
    row = provider.to_public_dict()
    row['suggested_models'] = [mid for mid in row['suggested_models'] if
                              'text_generation' in resolve(provider, mid).get('operations', [])]
    return row
