"""Host-first LAN device workers (Cuttle Workers mesh). See CUTTLE_WORKERS.md."""

from __future__ import annotations

__all__ = ["workers_enabled"]


def workers_enabled() -> bool:
    from api.device_workers.config import device_workers_enabled

    return device_workers_enabled()
