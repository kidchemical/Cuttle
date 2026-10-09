"""Cheap text completions for Cuttle-owned jobs.

Use this for chat titles, commit subjects, prompt enhance, and the router
brain. Guest agent CLIs do the actual work. All direct model transports live in api.model_providers; domain callers retain
their own prompts and fallback policies.

Provider and model resolution is owned by
:mod:`api.completion_providers` — this module only speaks to whichever
transport it is handed. Precedence for both: explicit call argument → per-call
env override (``CUTTLE_LLM_OPENAI_MODEL`` and friends) → Settings → provider
default.
"""

from __future__ import annotations

import re
import logging
from typing import Optional, Sequence

from api.completion_providers import get_provider, resolve_model, resolve_order, resolve_effort
from api.model_providers.service import complete_text as _complete_provider
from api.model_providers.types import TextRequest

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Kept as module-level names for callers/tests that import them; the values
# now come from the provider registry rather than being hardcoded here.
DEFAULT_OPENAI_MODEL = get_provider("openai").default_model
DEFAULT_ANTHROPIC_MODEL = get_provider("anthropic").default_model


def _clean(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _via_openai(user, *, system, model, max_tokens, temperature, timeout, json_object):
    """Compatibility entry; vendor behavior belongs to the provider adapter."""
    return _complete_provider("openai", TextRequest(user, system, model, max_tokens,
                                                   temperature, timeout, json_object))


def _via_anthropic(user, *, system, model, max_tokens, temperature):
    return _complete_provider("anthropic", TextRequest(user, system, model, max_tokens, temperature))


def _via_local(user, *, system, max_tokens, temperature, timeout, model=None):
    return _complete_provider("local", TextRequest(user, system, model or "", max_tokens,
                                                  temperature, timeout))


def complete(
    *,
    user: str,
    system: str = "",
    max_tokens: int = 200,
    temperature: float = 0.3,
    openai_model: Optional[str] = None,
    anthropic_model: Optional[str] = None,
    timeout: float = 45.0,
    json_object: bool = False,
    providers: Optional[Sequence[str]] = None,
    local_model: Optional[str] = None,
    effort: Optional[str] = None,
    task: Optional[str] = None,
    cancel_event=None,
) -> Optional[str]:
    """Return cleaned completion text, or None if every provider failed.

    The order is the provider registry's: the user's pinned provider (if any) first, then
    OpenAI → Anthropic → local. Pass ``providers`` to pin an exact chain
    (wrappers in titler/commit/enhance do this so tests can stub a hop).
    """
    if task or providers is None:
        from api.background_helpers import has_selection, generate
        if has_selection(task or "background"):
            result = generate(task or "background", TextRequest(user=user, system=system, max_tokens=max_tokens,
                temperature=temperature, timeout=timeout, json_object=json_object, effort=effort),
                cancel_event=cancel_event)
            return _clean(result.text) if result else None
    if providers is None:
        providers = resolve_order()
    explicit_models = {"openai": openai_model, "anthropic": anthropic_model, "local": local_model}
    for name in providers:
        n = str(name).strip().lower()
        try:
            model_id = resolve_model(n, explicit_models.get(n))
            text = _complete_provider(n, TextRequest(
                user=user, system=system, model=model_id,
                max_tokens=max_tokens, temperature=temperature, timeout=timeout,
                json_object=json_object, effort=resolve_effort(n, model_id, effort),
            ))
        except Exception as exc:
            # Report failure metadata without logging credentials, prompts or
            # provider response bodies. Callers may then fall back honestly.
            logging.getLogger(__name__).warning(
                "Completion provider %s failed (%s; status=%s)",
                n, type(exc).__name__, getattr(exc, "status_code", None),
            )
            continue
        cleaned = _clean(text or "")
        if cleaned:
            return cleaned
    return None
