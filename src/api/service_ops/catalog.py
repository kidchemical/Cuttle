"""Service-op registry — discovery + dispatch for external action types.

Bundled ops live in ``ops/`` (each module exposes ``build_op()``). Optional
drop-ins: ``<home>/personal/service_ops/*.py`` and ``CUTTLE_SERVICE_OPS_DIR``
(``os.pathsep``-separated). Every candidate is validated (id/action_types/
execute) before it is registered; a bad drop-in is skipped, never fatal.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import personal_dir

from api.service_ops.protocol import ServiceOp, ServiceOpInfo

_BUNDLED_DIR = Path(__file__).resolve().parent / "ops"

_registry: Optional[Dict[str, ServiceOp]] = None
_type_index: Dict[str, str] = {}


def _valid(op: Any) -> bool:
    return bool(getattr(op, "id", "")) and hasattr(op, "action_types") \
        and hasattr(op, "execute") and hasattr(op, "available")


def _register(op: ServiceOp) -> None:
    _registry[op.id] = op  # type: ignore[index]
    for action_type in op.action_types:
        _type_index.setdefault(str(action_type).strip().lower(), op.id)


def _build_from_module(module: Any) -> Optional[ServiceOp]:
    factory = getattr(module, "build_op", None)
    op = factory() if callable(factory) else getattr(module, "op", None)
    return op if op is not None and _valid(op) else None


def _load_bundled() -> None:
    if not _BUNDLED_DIR.is_dir():
        return
    for path in sorted(_BUNDLED_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"api.service_ops.ops.{path.stem}")
        except ImportError:
            continue
        op = _build_from_module(module)
        if op is not None:
            _register(op)


def _external_dirs() -> List[Path]:
    dirs = [personal_dir() / "service_ops"]
    env = (os.environ.get("CUTTLE_SERVICE_OPS_DIR") or "").strip()
    if env:
        dirs += [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    return dirs


def _load_external() -> None:
    for base in _external_dirs():
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"cuttle_service_op_{path.stem}", path)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception:
                continue
            op = _build_from_module(module)
            if op is not None and op.id not in _registry:
                _register(op)


def _ensure() -> Dict[str, ServiceOp]:
    global _registry
    if _registry is None:
        _registry = {}
        _type_index.clear()
        _load_bundled()
        _load_external()
    return _registry


def reset_cache() -> None:
    global _registry
    _registry = None


def register(op: ServiceOp) -> None:
    _ensure()
    _register(op)


def find_op(action_type: Optional[str]) -> Optional[ServiceOp]:
    """The op registered for an action type, or None (e.g. built-in ``shell``)."""
    registry = _ensure()
    key = str(action_type or "").strip().lower()
    op_id = _type_index.get(key)
    return registry.get(op_id) if op_id else None


def list_ops() -> List[ServiceOp]:
    return list(_ensure().values())


def ops_status() -> List[Dict[str, Any]]:
    return [
        {"id": op.id, "label": getattr(op, "label", op.id),
         "action_types": list(op.action_types)}
        for op in _ensure().values()
    ]


def op_info(op: ServiceOp) -> ServiceOpInfo:
    return ServiceOpInfo(id=op.id, label=getattr(op, "label", op.id),
                         action_types=tuple(op.action_types),
                         description=str(getattr(op, "description", "") or ""))
