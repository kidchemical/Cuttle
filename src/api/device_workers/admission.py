"""Resource admission for mesh jobs: does a job's declared need fit a worker now?

Pure: the store calls :func:`admit` inside its claim transaction with the
worker's latest heartbeat ``load`` (``capabilities.collect_capabilities``) and
the reservations its claimed/running jobs hold. A job declares
``requirements.resources``::

    {"ram_mb": 9000, "vram_mb": 7000, "gpu_slots": 1, "service": "comfyui"}

Headroom is the smaller of the reservation budget (total minus a host reserve,
minus what active jobs reserved) and the live free amount, so an app the mesh
does not own (a Unity Editor, a game) still blocks admission. Memory a named
``service`` already holds (ComfyUI's cached models) counts as free for jobs
that run inside that service, because the service reuses or evicts it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

HOST_RESERVE_MB = 4096  # RAM never reserved: the desktop, Flask, the daemon
RAM_MARGIN_MB = 1024
VRAM_MARGIN_MB = 512
TELEMETRY_STALE_SEC = 60.0


def requested(requirements: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The ``resources`` block of a job's requirements, normalised; ``{}`` when none."""
    raw = (requirements or {}).get("resources")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ("ram_mb", "vram_mb", "gpu_slots"):
        try:
            value = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            out[key] = value
    if out.get("vram_mb") and not out.get("gpu_slots"):
        out["gpu_slots"] = 1
    service = str(raw.get("service") or "").strip().lower()
    if service:
        out["service"] = service
    return out


def reserved_totals(reservations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum the reservations of a worker's active jobs (RAM, and VRAM/slots per GPU)."""
    ram = 0
    vram: Dict[int, int] = {}
    slots: Dict[int, int] = {}
    for res in reservations:
        if not isinstance(res, dict):
            continue
        ram += int(res.get("ram_mb") or 0)
        if res.get("gpu_index") is not None:
            idx = int(res["gpu_index"])
            vram[idx] = vram.get(idx, 0) + int(res.get("vram_mb") or 0)
            slots[idx] = slots.get(idx, 0) + int(res.get("gpu_slots") or 0)
    return {"ram_mb": ram, "vram_mb": vram, "gpu_slots": slots}


def _gb_to_mb(value: Any) -> Optional[int]:
    try:
        return int(float(value) * 1024)
    except (TypeError, ValueError):
        return None


def admit(
    need: Dict[str, Any],
    load: Dict[str, Any],
    reservations: List[Dict[str, Any]],
    *,
    telemetry_age: float,
    host_reserve_mb: int = HOST_RESERVE_MB,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(reservation, "")`` when ``need`` fits, else ``(None, reason)``."""
    if not need:
        return {}, ""
    if telemetry_age > TELEMETRY_STALE_SEC:
        return None, f"telemetry {int(telemetry_age)}s old"
    held = reserved_totals(reservations)
    service = (load.get("services") or {}).get(need.get("service") or "") or {}
    reservation: Dict[str, Any] = {}

    ram_mb = int(need.get("ram_mb") or 0)
    if ram_mb:
        total = _gb_to_mb(load.get("ram_total_gb"))
        available = _gb_to_mb(load.get("ram_available_gb"))
        if total is None or available is None:
            return None, "worker reports no RAM telemetry (update Cuttle on it)"
        available += int(service.get("rss_mb") or 0)
        headroom = min(total - host_reserve_mb - held["ram_mb"], available - RAM_MARGIN_MB)
        if ram_mb > headroom:
            return None, f"needs {ram_mb / 1024:.1f} GB RAM, {max(0, headroom) / 1024:.1f} GB free"
        reservation["ram_mb"] = ram_mb

    vram_mb = int(need.get("vram_mb") or 0)
    gpu_slots = int(need.get("gpu_slots") or 0)
    if vram_mb or gpu_slots:
        gpus = [g for g in (load.get("gpus") or []) if isinstance(g, dict)]
        if not gpus:
            return None, "worker reports no GPU telemetry"
        best: Optional[Tuple[int, int]] = None
        for gpu in gpus:
            idx = int(gpu.get("index") or 0)
            if held["gpu_slots"].get(idx, 0) + gpu_slots > 1:
                continue
            total = int(gpu.get("vram_total_mb") or 0)
            used = int(gpu.get("vram_used_mb") or 0) - int((service.get("vram_mb") or {}).get(str(idx)) or 0)
            headroom = min(total - VRAM_MARGIN_MB - held["vram_mb"].get(idx, 0), total - used - VRAM_MARGIN_MB)
            if headroom >= vram_mb and (best is None or headroom > best[1]):
                best = (idx, headroom)
        if best is None:
            free = max((int(g.get("vram_total_mb") or 0) - int(g.get("vram_used_mb") or 0) for g in gpus), default=0)
            busy = all(held["gpu_slots"].get(int(g.get("index") or 0), 0) for g in gpus)
            return None, "GPU busy with another job" if busy and gpu_slots else (
                f"needs {vram_mb / 1024:.1f} GB VRAM, {free / 1024:.1f} GB free")
        reservation.update({"gpu_index": best[0], "vram_mb": vram_mb, "gpu_slots": gpu_slots})
    if need.get("service"):
        reservation["service"] = need["service"]
    return reservation, ""
