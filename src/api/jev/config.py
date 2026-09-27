"""Jev settings — env + optional settings.json['jev']."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from api.jev.thresholds import REGRESS_INTERVAL_S, REGRESS_LOOKBACK_S

SETTINGS_KEY = "jev"
JEV_MODEL_ALIASES = frozenset({"jev", "jev-latest", "jev-1", "typesafe", "typesafe-jev"})
TYPESAFE_BASE_URL = "https://api.typesafe.ai"
OPENROUTER_SYSTEMONE_BASE = "https://openrouter.ai/api"


@dataclass
class JevRegressConfig:
    enabled: bool = True
    interval_s: int = REGRESS_INTERVAL_S
    lookback_s: int = REGRESS_LOOKBACK_S


@dataclass
class JevConfig:
    enabled: bool = True
    api_key: str = ""
    base_url: str = TYPESAFE_BASE_URL
    model: str = "jev-latest"
    timeout_s: float = 12.0
    rank_context: bool = True
    label_turns: bool = True
    auth_source: str = "none"
    regress: JevRegressConfig = field(default_factory=JevRegressConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.model,
            "base_url": self.base_url,
            "timeout_s": self.timeout_s,
            "rank_context": self.rank_context,
            "label_turns": self.label_turns,
            "has_api_key": bool(self.api_key),
            "auth_source": self.auth_source,
            "regress": {
                "enabled": self.regress.enabled,
                "interval_s": self.regress.interval_s,
                "lookback_s": self.regress.lookback_s,
            },
        }


def is_jev_model_id(model: str) -> bool:
    mid = (model or "").strip().lower()
    if mid.startswith("~"):
        mid = mid[1:]
    if mid.startswith("typesafe/"):
        mid = mid.split("/", 1)[-1]
    if mid in JEV_MODEL_ALIASES:
        return True
    return mid.startswith("jev-")


def resolve_jev_credentials(raw: Optional[Dict[str, Any]] = None) -> tuple[str, str, str]:
    """Return (api_key, base_url, auth_source).

    TypeSafe's native key wins. Otherwise OpenRouter hosts Jev at
    ``https://openrouter.ai/api/v1/systemone`` with ``OPENROUTER_API_KEY``.
    An explicit ``CUTTLE_JEV_BASE_URL`` / ``jev.base_url`` always wins on host.
    """
    blob = raw if isinstance(raw, dict) else {}
    typesafe = (os.environ.get("TYPESAFE_API_KEY") or blob.get("api_key") or "").strip()
    openrouter = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    explicit_base = str(
        blob.get("base_url") or os.environ.get("CUTTLE_JEV_BASE_URL") or ""
    ).strip().rstrip("/")
    if typesafe:
        return typesafe, explicit_base or TYPESAFE_BASE_URL, "typesafe"
    if openrouter:
        return openrouter, explicit_base or OPENROUTER_SYSTEMONE_BASE, "openrouter"
    return "", explicit_base or TYPESAFE_BASE_URL, "none"


def load_jev_config() -> JevConfig:
    raw: Dict[str, Any] = {}
    try:
        from managers.settings_manager import get_settings_manager

        sm = get_settings_manager()
        try:
            sm.reload()
        except Exception:
            pass
        got = sm.get_setting(SETTINGS_KEY)
        if isinstance(got, dict):
            raw = got
    except Exception:
        raw = {}

    enabled = raw.get("enabled", True) is not False
    if os.environ.get("CUTTLE_JEV_DISABLED", "").strip() in ("1", "true", "yes"):
        enabled = False
    key, base_url, auth_source = resolve_jev_credentials(raw)
    model = str(raw.get("model") or os.environ.get("CUTTLE_JEV_MODEL") or "jev-latest").strip()
    if is_jev_model_id(model) and model.lower() in (
        "jev",
        "typesafe",
        "typesafe-jev",
        "typesafe/jev",
        "~typesafe/jev-latest",
    ):
        model = "jev-latest"
    try:
        timeout_s = float(raw.get("timeout_s") or os.environ.get("CUTTLE_JEV_TIMEOUT") or 12.0)
    except (TypeError, ValueError):
        timeout_s = 12.0
    regress_raw = raw.get("regress") if isinstance(raw.get("regress"), dict) else {}
    try:
        interval = int(regress_raw.get("interval_s") or REGRESS_INTERVAL_S)
    except (TypeError, ValueError):
        interval = REGRESS_INTERVAL_S
    try:
        lookback = int(regress_raw.get("lookback_s") or REGRESS_LOOKBACK_S)
    except (TypeError, ValueError):
        lookback = REGRESS_LOOKBACK_S
    return JevConfig(
        enabled=enabled,
        api_key=key,
        base_url=base_url,
        model=model or "jev-latest",
        timeout_s=max(2.0, min(timeout_s, 60.0)),
        rank_context=raw.get("rank_context", True) is not False,
        label_turns=raw.get("label_turns", True) is not False,
        auth_source=auth_source,
        regress=JevRegressConfig(
            enabled=regress_raw.get("enabled", True) is not False,
            interval_s=max(60, min(interval, 86400)),
            lookback_s=max(60, min(lookback, 86400 * 7)),
        ),
    )
