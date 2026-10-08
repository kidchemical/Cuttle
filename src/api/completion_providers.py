"""Which provider and model serve Cuttle's own cheap completions.

Chat titles, commit subjects, prompt enhance, and the router brain all funnel
through :func:`api.llm_complete.complete`. Until now that function hardcoded
``gpt-4o-mini`` / ``claude-haiku-4-5`` and read an env var no UI ever wrote, so
the two "cheap completion model" dropdowns in Settings were dead controls:
they POSTed ``preferred_llm_model`` / ``preferred_tools_ollama_model`` and
nothing consulted either one.

This module is the single answer. It is deliberately small and declarative:

* a provider is a row (id, label, credential env names, model env override,
  default model, curated suggestions) — adding one is a data edit, not a fork,
* preference is stored as one settings key (``completion_provider`` /
  ``completion_models``) rather than the legacy split "cloud vs tools" pair,
* the resolution order is fixed and documented: explicit argument → per-call
  env override → Settings → provider default. Every hop is overridable, so an
  operator who prefers ``.env`` keeps working with no Settings write at all.

Precedence note: this registry resolves *which provider/model*. Credential
availability still comes from the environment, and providers with no
credential simply report themselves unavailable instead of raising.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

SETTINGS_KEY = 'completion_provider'
MODELS_KEY = 'completion_models'
# Providers are tried in this order when the user has not pinned one.
DEFAULT_ORDER: Tuple[str, ...] = ('openai', 'anthropic', 'local')


class CompletionProvider:
    """One cheap-completion backend. Data only — no transport here."""

    __slots__ = (
        'id', 'label', 'credential_env', 'model_env', 'default_model',
        'suggested_models', 'needs_credential',
    )

    def __init__(
        self,
        id: str,
        label: str,
        *,
        credential_env: Tuple[str, ...] = (),
        model_env: str = '',
        default_model: str = '',
        suggested_models: Tuple[str, ...] = (),
        needs_credential: bool = True,
    ) -> None:
        self.id = id
        self.label = label
        self.credential_env = credential_env
        self.model_env = model_env
        self.default_model = default_model
        self.suggested_models = suggested_models
        self.needs_credential = needs_credential

    def credential_present(self) -> bool:
        return any((os.getenv(name) or '').strip() for name in self.credential_env)

    def configured(self) -> bool:
        """Can this provider actually serve a completion right now?

        A missing credential is the common OOBE case and must read as
        "not configured", not as an error at call time.
        """
        if not self.needs_credential:
            return True
        return self.credential_present()

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'label': self.label,
            'credential_env': list(self.credential_env),
            'default_model': self.default_model,
            'suggested_models': list(self.suggested_models),
            'needs_credential': self.needs_credential,
            'credential_present': self.credential_present(),
            'configured': self.configured(),
        }


_REGISTRY: Tuple[CompletionProvider, ...] = (
    CompletionProvider(
        'openai',
        'OpenAI',
        credential_env=('OPENAI_API_KEY', 'API_KEY'),
        model_env='CUTTLE_LLM_OPENAI_MODEL',
        default_model='gpt-4o-mini',
        suggested_models=(
            'gpt-4o-mini',
            'gpt-4o',
            'gpt-4.1-mini',
            'gpt-4.1',
        ),
    ),
    CompletionProvider(
        'anthropic',
        'Anthropic',
        credential_env=('ANTHROPIC_API_KEY',),
        model_env='CUTTLE_LLM_ANTHROPIC_MODEL',
        default_model='claude-haiku-4-5-20251001',
        suggested_models=(
            'claude-haiku-5-5',
            'claude-haiku-4-5-20251001',
            'claude-sonnet-4-20250514',
        ),
    ),
    CompletionProvider(
        'local',
        'Local (Ollama / llama.cpp)',
        credential_env=(),
        model_env='OLLAMA_MODEL',
        default_model='',
        needs_credential=False,
    ),
)

_BY_ID: Dict[str, CompletionProvider] = {p.id: p for p in _REGISTRY}


def get_provider(provider_id: Optional[str]) -> Optional[CompletionProvider]:
    return _BY_ID.get(str(provider_id or '').strip().lower())


def list_providers() -> List[Dict[str, Any]]:
    """Public shape for the Settings UI."""
    return [p.to_public_dict() for p in _REGISTRY]


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
        row = provider.to_public_dict()
        row['effective_model'] = resolve_model(provider.id)
        row['model_from'] = (
            'settings' if provider.id in models
            else 'env' if provider.model_env and (os.getenv(provider.model_env) or '').strip()
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
    }