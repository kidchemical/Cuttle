"""Cheap text completions for Cuttle-owned jobs.

Use this for chat titles, commit subjects, prompt enhance, and the router
brain. Guest agent CLIs do the actual work. TTS, vision, and Jev keep their
own clients.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Sequence

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


def _clean(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _messages(user: str, system: str) -> list:
    msgs = []
    if (system or "").strip():
        msgs.append({"role": "system", "content": system.strip()})
    msgs.append({"role": "user", "content": user})
    return msgs


def _via_openai(
    user: str,
    *,
    system: str,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
    json_object: bool,
) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        return None
    from openai import OpenAI

    kwargs = {
        "model": model,
        "messages": _messages(user, system),
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    if json_object:
        kwargs["response_format"] = {"type": "json_object"}
    client = OpenAI(api_key=api_key, timeout=float(timeout))
    resp = client.chat.completions.create(**kwargs)
    if not resp.choices:
        return None
    return resp.choices[0].message.content or ""


def _via_anthropic(
    user: str,
    *,
    system: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> Optional[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    from anthropic import Anthropic

    kwargs = {
        "model": model,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "messages": [{"role": "user", "content": user}],
    }
    if (system or "").strip():
        kwargs["system"] = system.strip()
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(**kwargs)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _via_local(
    user: str,
    *,
    system: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> Optional[str]:
    from openai import OpenAI
    from core.local_llm import (
        get_local_api_key,
        get_local_base_url,
        local_reachable,
        resolve_local_model,
    )

    if not local_reachable(timeout=1.5):
        return None
    client = OpenAI(
        base_url=get_local_base_url(),
        api_key=get_local_api_key(),
        timeout=float(timeout),
    )
    resp = client.chat.completions.create(
        model=resolve_local_model(None),
        messages=_messages(user, system),
        temperature=float(temperature),
        max_tokens=int(max_tokens),
    )
    if not resp.choices:
        return None
    return resp.choices[0].message.content or ""


def complete(
    *,
    user: str,
    system: str = "",
    max_tokens: int = 200,
    temperature: float = 0.3,
    inference_mode: str = "auto",
    openai_model: Optional[str] = None,
    anthropic_model: Optional[str] = None,
    timeout: float = 45.0,
    json_object: bool = False,
    providers: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Return cleaned completion text, or None if every provider failed.

    ``inference_mode=local`` uses only the local server. Otherwise the default
    order is OpenAI → Anthropic → local. Pass ``providers`` to pin one hop
    (wrappers in titler/commit/enhance do this so tests can stub a hop).
    """
    mode = (inference_mode or "auto").strip().lower()
    if providers is None:
        providers = ("local",) if mode == "local" else ("openai", "anthropic", "local")
    oai = (
        openai_model
        or os.getenv("CUTTLE_LLM_OPENAI_MODEL")
        or DEFAULT_OPENAI_MODEL
    ).strip()
    ant = (
        anthropic_model
        or os.getenv("CUTTLE_LLM_ANTHROPIC_MODEL")
        or DEFAULT_ANTHROPIC_MODEL
    ).strip()
    for name in providers:
        n = str(name).strip().lower()
        try:
            if n == "openai":
                text = _via_openai(
                    user,
                    system=system,
                    model=oai,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    json_object=json_object,
                )
            elif n == "anthropic":
                if json_object:
                    continue
                text = _via_anthropic(
                    user,
                    system=system,
                    model=ant,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            elif n == "local":
                text = _via_local(
                    user,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=max(timeout, 90.0),
                )
            else:
                continue
        except Exception:
            continue
        cleaned = _clean(text or "")
        if cleaned:
            return cleaned
    return None
