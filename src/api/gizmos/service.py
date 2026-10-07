"""Gizmo semantics shared by HTTP routes and the agent CLI.

Placement is the one cross-type contract::

    {"dock": "titlebar" | "rail" | "float" | "popout",
     "order": float,          # position within titlebar/rail docks
     "x": 0..1, "y": 0..1}    # viewport fractions for float

``titlebar`` falls back to ``rail`` on surfaces without the Electron title
bar, and ``popout`` (an always-on-top desktop window) falls back to ``float``
outside the desktop app — that is a client rendering rule, the stored dock is
kept so the next desktop session restores it.
"""

from __future__ import annotations

import math
import re
import uuid
from typing import Any, Dict, List, Optional

from api.gizmos import catalog, store

DOCKS = ("titlebar", "rail", "float", "popout")
CREATED_BY = ("ui", "agent", "cli")
MAX_GIZMOS = 24
MAX_TITLE = 60
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")


class GizmoError(ValueError):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def _finite(value: Any, lo: float, hi: float) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return max(lo, min(hi, out))


def normalize_placement(raw: Any, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"dock": "titlebar", "order": 0.0}
    out.update({k: v for k, v in (existing or {}).items() if k in ("dock", "order", "x", "y")})
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise GizmoError("placement must be an object")
    if "dock" in raw:
        dock = str(raw.get("dock") or "").strip().lower()
        if dock not in DOCKS:
            raise GizmoError(f"dock must be one of {', '.join(DOCKS)}")
        out["dock"] = dock
    if raw.get("order") is not None:
        order = _finite(raw.get("order"), -1e6, 1e6)
        if order is None:
            raise GizmoError("order must be a number")
        out["order"] = order
    for axis in ("x", "y"):
        if raw.get(axis) is not None:
            value = _finite(raw.get(axis), 0.0, 1.0)
            if value is None:
                raise GizmoError(f"{axis} must be a number between 0 and 1")
            out[axis] = round(value, 4)
    return out


def _title(raw: Any, fallback: str) -> str:
    title = re.sub(r"\s+", " ", str(raw or "")).strip()
    return (title or fallback)[:MAX_TITLE]


def public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: row.get(k) for k in ("id", "type", "title", "config", "placement", "created_by",
                                    "created_at", "updated_at", "revision")}


def list_payload() -> Dict[str, Any]:
    return {"revision": store.current_revision(),
            "gizmos": [public(r) for r in store.list_gizmos()]}


def get(gizmo_id: str) -> Dict[str, Any]:
    row = store.get_gizmo(str(gizmo_id or ""))
    if not row:
        raise GizmoError(f"gizmo {gizmo_id!r} not found", "not_found")
    return public(row)


def _next_order(dock: str) -> float:
    orders = [float(r["placement"].get("order") or 0) for r in store.list_gizmos()
              if r["placement"].get("dock") == dock]
    return (max(orders) + 1.0) if orders else 0.0


def create(type_id: str, *, config: Optional[Dict[str, Any]] = None,
           placement: Optional[Dict[str, Any]] = None, title: Optional[str] = None,
           gizmo_id: Optional[str] = None, created_by: str = "ui", actor=None) -> Dict[str, Any]:
    spec = catalog.get(type_id)
    if spec is None:
        known = ", ".join(t.id for t in catalog.all_types())
        raise GizmoError(f"unknown gizmo type {type_id!r} (known: {known})")
    if spec.id == "tasks":
        raise GizmoError("Tasks are chat-scoped: use python -m api.gizmos tasks create or POST /api/gizmos/tasks")
    if store.count() >= MAX_GIZMOS:
        raise GizmoError(f"gizmo limit reached ({MAX_GIZMOS}); remove one first")
    gid = str(gizmo_id or "").strip().lower() or f"{spec.id.replace('_', '-')}-{uuid.uuid4().hex[:8]}"
    if not _ID_RE.fullmatch(gid):
        raise GizmoError("id must be 1-48 chars: lowercase letters, digits, '-' or '_'")
    if store.get_gizmo(gid):
        raise GizmoError(f"gizmo {gid!r} already exists", "conflict")
    try:
        cfg = spec.normalize_config(config or {}, None)
    except ValueError as exc:
        raise GizmoError(str(exc)) from exc
    place = normalize_placement(placement)
    if not placement or placement.get("order") is None:
        place["order"] = _next_order(place["dock"])
    who = created_by if created_by in CREATED_BY else "ui"
    row = store.insert_gizmo(gizmo_id=gid, gtype=spec.id, title=_title(title, spec.default_title(cfg)),
                             config=cfg, placement=place, created_by=who, actor=actor)
    return public(row)


def update(gizmo_id: str, *, title: Optional[str] = None, config: Optional[Dict[str, Any]] = None,
           placement: Optional[Dict[str, Any]] = None, actor=None) -> Dict[str, Any]:
    row = store.get_gizmo(str(gizmo_id or ""))
    if not row:
        raise GizmoError(f"gizmo {gizmo_id!r} not found", "not_found")
    spec = catalog.get(row["type"])
    if spec is None:
        raise GizmoError(f"gizmo type {row['type']!r} is no longer registered")
    cfg = row["config"]
    if config is not None:
        if not isinstance(config, dict):
            raise GizmoError("config must be an object")
        try:
            cfg = spec.normalize_config(config, row["config"])
        except ValueError as exc:
            raise GizmoError(str(exc)) from exc
    place = normalize_placement(placement, row["placement"])
    if placement and "dock" in placement and placement.get("order") is None \
            and place["dock"] != row["placement"].get("dock"):
        place["order"] = _next_order(place["dock"])
    old_default = spec.default_title(row["config"])
    if title is not None:
        new_title = _title(title, spec.default_title(cfg))
    elif row["title"] == old_default:
        new_title = spec.default_title(cfg)  # keep auto titles in step with the agent
    else:
        new_title = row["title"]
    updated = store.update_gizmo(row["id"], title=new_title, config=cfg, placement=place, actor=actor)
    if not updated:
        raise GizmoError(f"gizmo {gizmo_id!r} not found", "not_found")
    return public(updated)


def move(gizmo_id: str, dock: str, *, order: Optional[float] = None,
         x: Optional[float] = None, y: Optional[float] = None, actor=None) -> Dict[str, Any]:
    placement: Dict[str, Any] = {"dock": dock}
    for key, value in (("order", order), ("x", x), ("y", y)):
        if value is not None:
            placement[key] = value
    return update(gizmo_id, placement=placement, actor=actor)


def remove(gizmo_id: str, *, actor=None) -> bool:
    if not store.delete_gizmo(str(gizmo_id or ""), actor=actor):
        raise GizmoError(f"gizmo {gizmo_id!r} not found", "not_found")
    return True


def resolve_data(gizmo_id: str, *, refresh: bool = False) -> Dict[str, Any]:
    gizmo = get(gizmo_id)
    spec = catalog.get(gizmo["type"])
    if spec is None:
        raise GizmoError(f"gizmo type {gizmo['type']!r} is no longer registered")
    try:
        return spec.resolve_data(gizmo["config"], refresh)
    except ValueError as exc:
        raise GizmoError(str(exc)) from exc


def types() -> List[Dict[str, Any]]:
    return catalog.types_payload()
