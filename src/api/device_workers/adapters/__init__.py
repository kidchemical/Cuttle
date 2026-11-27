"""Stage adapters: mesh job types that drive a long-running provider.

Each adapter module exposes ``TYPE`` (the job type it runs) and two functions:

``validate(params) -> (ok, error)``
    Host-side submit check (no network, no provider calls).
``run(params) -> result``
    Worker-side. Reports progress through ``params["_on_progress"]`` with the
    store's progress fields (``units_done``/``units_total``, ``stage``,
    ``step``/``steps``, ``message``, ``preview_url``), stops when
    ``params["_cancelled"]()`` turns true, and recovers its provider-side work
    (same external id) when the job is retried after a worker restart.
    Raises ``JobExecError`` for permanent failures.

The built-in executor types (``shell`` recipes for Unity builds, ``blender_render``,
``file_copy``) stay in ``executor.py``; new providers go here.
"""

from __future__ import annotations

from types import ModuleType
from typing import Dict, List, Optional

from api.device_workers.adapters import comfyui

_ADAPTERS: Dict[str, ModuleType] = {mod.TYPE: mod for mod in (comfyui,)}


def get(job_type: str) -> Optional[ModuleType]:
    return _ADAPTERS.get((job_type or "").strip())


def names() -> List[str]:
    return sorted(_ADAPTERS)
