"""OpenRouter Management Key — credential access and account credits.

The **Management (provisioning)** key is a separate, account-level credential
from the regular completion ``OPENROUTER_API_KEY``: it can read account credits
and manage that account's API keys, and it *cannot* run completions. Cuttle
stores it as its own secret in ``<home>/.env`` and reads it here, so the
Settings probe and the (future) usage-meter provider share one owner.

Endpoints (https://openrouter.ai/docs):

- ``GET /api/v1/credits`` -> ``{total_credits, total_usage}`` — management key
  only; a normal API key answers ``403``.
- ``GET /api/v1/key`` -> per-key ``limit`` / ``limit_remaining`` /
  ``usage_daily|weekly|monthly`` — accepted by either key; reserved for a
  budget/readout variant later.

This module never imports the Flask monolith (owned layer, reverse-import
rule); the settings routes delegate to :func:`management_key` / :func:`probe`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from core.runtime_paths import env_file

ENV_NAME = "OPENROUTER_MANAGEMENT_KEY"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
KEY_URL = "https://openrouter.ai/api/v1/key"
DEFAULT_TIMEOUT_S = 20.0


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN


def _read_env_file(names) -> Dict[str, str]:
    """Values for ``names`` from ``<home>/.env`` (comments/exports tolerated)."""
    out: Dict[str, str] = {}
    try:
        path = env_file()
        if not path.is_file():
            return out
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in line:
                    continue
                lhs, _, rhs = line.partition("=")
                key = lhs.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                if key in names:
                    out[key] = rhs.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def management_key() -> str:
    """Saved management key: process env first, then ``<home>/.env``."""
    value = (os.environ.get(ENV_NAME) or "").strip()
    if value:
        return value
    return _read_env_file({ENV_NAME}).get(ENV_NAME, "").strip()


def has_management_key() -> bool:
    return bool(management_key())


def _get_json(url: str, token: str, *, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Cuttle-OpenRouterCredits/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {"success": False, "error": "OpenRouter rejected the management key (401)."}
        if exc.code == 403:
            return {"success": False, "error": (
                "This key is not a Management key (403). Create one at "
                "openrouter.ai/settings/management-keys.")}
        return {"success": False, "error": f"OpenRouter returned HTTP {exc.code}."}
    except Exception as exc:  # noqa: BLE001 - surface any transport failure
        return {"success": False, "error": f"OpenRouter request failed: {exc}"}
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {"success": False, "error": "OpenRouter returned invalid JSON."}
    if not isinstance(payload, dict):
        return {"success": False, "error": "Unexpected OpenRouter payload."}
    return {"success": True, "payload": payload}


def fetch_credits(key: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """Account credits via ``GET /api/v1/credits`` (management key).

    Returns ``{success, total_credits, total_usage, balance, error}`` where
    ``balance = total_credits - total_usage``.
    """
    token = (key or management_key() or "").strip()
    if not token:
        return {"success": False, "error": "No OpenRouter management key saved."}
    got = _get_json(CREDITS_URL, token, timeout=timeout)
    if not got.get("success"):
        return got
    data = (got["payload"] or {}).get("data")
    if not isinstance(data, dict):
        return {"success": False, "error": "Unexpected OpenRouter credits payload."}
    total = _num(data.get("total_credits"))
    used = _num(data.get("total_usage"))
    balance = None if total is None or used is None else round(total - used, 6)
    return {"success": True, "total_credits": total, "total_usage": used, "balance": balance}


def probe(key: Optional[str] = None) -> Dict[str, Any]:
    """Settings "Test" probe: validate the key and report the credit balance."""
    result = fetch_credits(key)
    if not result.get("success"):
        return {"success": False, "message": str(result.get("error") or "OpenRouter test failed")}
    balance = result.get("balance")
    total = result.get("total_credits")
    used = result.get("total_usage")
    parts = []
    if balance is not None:
        parts.append(f"${balance:,.2f} credit left")
    if total is not None and used is not None:
        parts.append(f"${used:,.2f} of ${total:,.2f} used")
    detail = " · ".join(parts) if parts else "credits available"
    return {"success": True, "message": f"Valid OpenRouter management key — {detail}"}
