"""Gizmo type vocabulary.

A gizmo type owns three things: config validation, a default title, and how
to resolve live data for one instance. The store never interprets ``config``;
the shell renders by ``type``. Adding a type is one ``register_type`` call
here plus its renderer in ``src/web/js/gizmos/gizmos_model.js``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from api.gizmos import usage


@dataclass(frozen=True)
class GizmoType:
    id: str
    label: str
    description: str
    icon: str
    normalize_config: Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Dict[str, Any]]
    default_title: Callable[[Dict[str, Any]], str]
    resolve_data: Callable[[Dict[str, Any], bool], Dict[str, Any]]
    options: Callable[[], Dict[str, Any]] = field(default=lambda: {})

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "label": self.label, "description": self.description,
                "icon": self.icon, "options": self.options()}


_TYPES: Dict[str, GizmoType] = {}


def register_type(spec: GizmoType) -> None:
    _TYPES[spec.id] = spec


def get(type_id: Any) -> Optional[GizmoType]:
    return _TYPES.get(str(type_id or "").strip().lower())


def all_types() -> List[GizmoType]:
    return list(_TYPES.values())


def types_payload() -> List[Dict[str, Any]]:
    return [t.to_dict() for t in _TYPES.values()]


# ── usage_meter ──────────────────────────────────────────────────────────────

USAGE_SHOW = ("remaining", "used")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _usage_config(raw: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = dict(existing or {})
    raw = raw if isinstance(raw, dict) else {}
    agent = str(raw.get("agent", base.get("agent", "")) or "").strip().lower()
    if not agent:
        known = [p["agent"] for p in usage.providers()]
        agent = known[0] if known else ""
    if usage.get_provider(agent) is None:
        known = ", ".join(p["agent"] for p in usage.providers())
        raise ValueError(f"unknown usage agent {agent!r} (known: {known})")
    window = str(raw.get("window", base.get("window", "tightest")) or "tightest").strip().lower()
    if window != "tightest" and not re.fullmatch(r"[a-z0-9_]{1,40}", window):
        raise ValueError("window must be 'tightest' or a window id like 'five_hour'")
    show = str(raw.get("show", base.get("show", "remaining")) or "remaining").strip().lower()
    if show not in USAGE_SHOW:
        raise ValueError("show must be 'remaining' or 'used'")
    if "notify_on_unblock" in raw:
        notify = _truthy(raw.get("notify_on_unblock"))
    else:
        notify = _truthy(base.get("notify_on_unblock", False))
    return {"agent": agent, "window": window, "show": show, "notify_on_unblock": notify}


def _usage_title(config: Dict[str, Any]) -> str:
    provider = usage.get_provider(config.get("agent"))
    return f"{provider.label if provider else 'Agent'} usage"


def _usage_data(config: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    return usage.snapshot(config.get("agent"), force=refresh)


def _usage_options() -> Dict[str, Any]:
    return {"agents": usage.providers(), "show": list(USAGE_SHOW)}


register_type(GizmoType(
    id="usage_meter",
    label="Usage meter",
    description="Plan budget left and when the next limit resets or unblocks — the /usage insight as a pinnable meter.",
    icon="⏱",
    normalize_config=_usage_config,
    default_title=_usage_title,
    resolve_data=_usage_data,
    options=_usage_options,
))
