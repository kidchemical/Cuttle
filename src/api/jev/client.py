"""Thin HTTP client for TypeSafe System One (Jev). No SDK dependency."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from api.jev.types import QuestionMap, State, SystemOneResult, parse_answer

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_S = 12.0

_OVERRIDE: Optional["JevClient"] = None


class JevError(Exception):
    def __init__(self, message: str, *, retryable: bool = True, status: int = 0):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class JevClient:
    """POST /v1/systemone. Policy stays in callers — this only transports."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        if api_key is None or base_url is None:
            from api.jev.config import resolve_jev_credentials

            resolved_key, resolved_base, _src = resolve_jev_credentials()
            if api_key is None:
                api_key = resolved_key
            if base_url is None:
                base_url = resolved_base
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or os.environ.get("CUTTLE_JEV_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = (model or os.environ.get("CUTTLE_JEV_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.timeout_s = float(timeout_s)

    def system_one(
        self,
        state: State,
        questions: QuestionMap,
        *,
        model: Optional[str] = None,
    ) -> SystemOneResult:
        if not questions:
            raise JevError("questions required", retryable=False)
        if not self.api_key:
            raise JevError(
                "No Jev key: set OPENROUTER_API_KEY (OpenRouter hosts Jev) or TYPESAFE_API_KEY",
                retryable=False,
            )
        payload = {
            "state": state,
            "model": (model or self.model).strip() or self.model,
            "questions": questions,
        }
        url = f"{self.base_url}/v1/systemone"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw_text = resp.read().decode("utf-8", errors="replace")
                status = int(getattr(resp, "status", 200) or 200)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            retryable = e.code in (408, 409, 429) or e.code >= 500
            raise JevError(
                f"Jev HTTP {e.code}: {err_body or e.reason}",
                retryable=retryable,
                status=int(e.code),
            ) from e
        except urllib.error.URLError as e:
            raise JevError(f"Jev transport: {e.reason}", retryable=True) from e
        except TimeoutError as e:
            raise JevError("Jev timed out", retryable=True) from e
        try:
            data = json.loads(raw_text) if raw_text else {}
        except json.JSONDecodeError as e:
            raise JevError("Jev response was not JSON", retryable=True) from e
        if not isinstance(data, dict):
            raise JevError("Jev response was not an object", retryable=True)
        if status >= 400:
            raise JevError(f"Jev HTTP {status}", retryable=status >= 500, status=status)
        answers_raw = data.get("answers") if isinstance(data.get("answers"), dict) else {}
        answers = {str(k): parse_answer(v) for k, v in answers_raw.items()}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return SystemOneResult(
            model=str(data.get("model") or payload["model"]),
            answers=answers,
            usage=dict(usage),
            raw=data,
        )


class FakeJevClient(JevClient):
    """Deterministic stand-in for tests. ``handler(state, questions) -> answers dict``."""

    def __init__(
        self,
        handler: Optional[Callable[[State, QuestionMap], Dict[str, Any]]] = None,
        *,
        canned: Optional[Dict[str, Any]] = None,
        model: str = "jev-fake",
    ):
        super().__init__(api_key="fake", model=model, timeout_s=1.0)
        self.handler = handler
        self.canned = canned or {}

    def system_one(
        self,
        state: State,
        questions: QuestionMap,
        *,
        model: Optional[str] = None,
    ) -> SystemOneResult:
        payload = self.canned
        if self.handler is not None:
            payload = self.handler(state, questions) or {}
        if not isinstance(payload, dict):
            payload = {}
        # Allow {key: answer_dict} or full {answers: {...}}
        answers_raw = payload.get("answers") if "answers" in payload else payload
        if not isinstance(answers_raw, dict):
            answers_raw = {}
        answers = {str(k): parse_answer(v) for k, v in answers_raw.items()}
        return SystemOneResult(
            model=model or self.model,
            answers=answers,
            usage={"input_tokens": 0, "output_tokens": 0},
            raw={"answers": answers_raw, "fake": True},
        )


def set_client_override(client: Optional[JevClient]) -> None:
    global _OVERRIDE
    _OVERRIDE = client


def get_client(*, timeout_s: Optional[float] = None) -> JevClient:
    if _OVERRIDE is not None:
        return _OVERRIDE
    from api.jev.config import load_jev_config

    cfg = load_jev_config()
    return JevClient(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        model=cfg.model,
        timeout_s=float(timeout_s if timeout_s is not None else cfg.timeout_s),
    )


def jev_available() -> bool:
    if os.environ.get("CUTTLE_JEV_DISABLED", "").strip() in ("1", "true", "yes"):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and _OVERRIDE is None:
        # Tests must opt in via set_client_override — never hit the live API.
        return False
    if _OVERRIDE is not None:
        return True
    try:
        from api.jev.config import load_jev_config

        cfg = load_jev_config()
        return bool(cfg.enabled and cfg.api_key)
    except Exception:
        return bool((os.environ.get("TYPESAFE_API_KEY") or "").strip())
