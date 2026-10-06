"""Host-side mesh intent helpers (agent-agnostic).

Heuristics only — no per-harness prompts. Used by `workers.plan` / blender shard
and (later) automatic enqueue when `device_workers.auto_mesh` is enabled.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


_BLENDER_RE = re.compile(
    r"\b(blender|render\s+frames?|frame\s*range|\.blend\b|optix|cycles|eevee)\b",
    re.I,
)
_COPY_RE = re.compile(
    r"\b(copy|file_copy|transfer|sync)\b.*\b(laptop|desktop|worker|tower|pc|machine|device)\b"
    r"|\b(from|to)\s+(the\s+)?(laptop|desktop|tower|another\s+(machine|device|pc))\b",
    re.I,
)
_EXPLICIT_MESH_RE = re.compile(
    r"\b(use\s+workers?|worker\s+mesh|across\s+(devices?|machines?|pcs?)|"
    r"farm\s+(this|the|frames?)|distribute\s+(this|the|frames?|render))\b",
    re.I,
)


def classify_message(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    reasons: List[str] = []
    kind = "none"
    if not raw:
        return {"mesh": False, "kind": "none", "reasons": ["empty"], "confidence": 0.0}

    if _EXPLICIT_MESH_RE.search(raw):
        reasons.append("explicit_use_workers")
    if _BLENDER_RE.search(raw):
        kind = "blender_render"
        reasons.append("blender_keywords")
    elif _COPY_RE.search(raw):
        kind = "file_copy"
        reasons.append("cross_device_copy")

    mesh = bool(reasons)
    if kind == "none" and mesh:
        kind = "generic"
    conf = 0.0
    if "explicit_use_workers" in reasons:
        conf = 0.9
    elif kind == "blender_render":
        conf = 0.75
    elif kind == "file_copy":
        conf = 0.65
    return {
        "mesh": mesh and conf >= 0.6,
        "kind": kind if mesh else "none",
        "reasons": reasons,
        "confidence": conf,
        "message_preview": raw[:200],
    }


def shard_frame_ranges(
    frame_start: int, frame_end: int, *, worker_count: int
) -> List[Tuple[int, int]]:
    """Equal-count split across N workers (pinned affinity mode)."""
    fs = int(frame_start)
    fe = int(frame_end)
    n = max(1, int(worker_count))
    total = fe - fs + 1
    if total <= 0:
        return []
    n = min(n, total)
    base = total // n
    rem = total % n
    out: List[Tuple[int, int]] = []
    cur = fs
    for i in range(n):
        span = base + (1 if i < rem else 0)
        end = cur + span - 1
        out.append((cur, end))
        cur = end + 1
    return out


def auto_chunk_size(
    total_frames: int,
    worker_count: int,
    *,
    min_size: int = 1,
    max_size: int = 8,
) -> int:
    """Pick a steal-friendly chunk size (generic — not engine-specific).

    Targets ~8 chunks per worker so a slow peer cannot strand a large sticky
    range. Clamped to [min_size, max_size]; callers pass ``chunk_size`` when
    they know unit cost.
    """
    total = max(0, int(total_frames))
    if total <= 0:
        return 1
    wc = max(1, int(worker_count))
    target_chunks = max(wc * 8, wc + 1)
    size = max(int(min_size), (total + target_chunks - 1) // target_chunks)
    return min(int(max_size), size, total)


def chunk_frame_ranges(
    frame_start: int, frame_end: int, *, chunk_size: int
) -> List[Tuple[int, int]]:
    """Split a frame range into fixed-size chunks for work-stealing queues."""
    fs = int(frame_start)
    fe = int(frame_end)
    size = max(1, int(chunk_size))
    if fe < fs:
        return []
    out: List[Tuple[int, int]] = []
    cur = fs
    while cur <= fe:
        end = min(cur + size - 1, fe)
        out.append((cur, end))
        cur = end + 1
    return out


def pick_blender_workers(
    workers: List[Dict[str, Any]],
    *,
    prefer_remote: bool = True,
    self_worker_id: str = "",
) -> List[Dict[str, Any]]:
    """Online workers advertising blender; prefer remote / non-interactive."""
    candidates: List[Dict[str, Any]] = []
    for w in workers:
        if not w.get("online"):
            continue
        caps = w.get("capabilities") if isinstance(w.get("capabilities"), dict) else {}
        if not caps.get("blender"):
            continue
        candidates.append(w)

    def sort_key(w: Dict[str, Any]) -> Tuple[int, int, float]:
        is_self = 1 if str(w.get("worker_id") or "") == self_worker_id or w.get("is_self") else 0
        interactive = 1 if str(w.get("interactive_priority") or "") == "high" else 0
        load = w.get("load") if isinstance(w.get("load"), dict) else {}
        gpu = float(load.get("gpu_pct") or 0)
        # prefer remote (is_self=0), non-interactive, lower GPU load
        remote_penalty = is_self if prefer_remote else 0
        return (remote_penalty, interactive, gpu)

    candidates.sort(key=sort_key)
    return candidates


def plan_from_message(
    text: str,
    *,
    workers: Optional[List[Dict[str, Any]]] = None,
    self_worker_id: str = "",
) -> Dict[str, Any]:
    """Return a mesh plan (does not enqueue)."""
    classification = classify_message(text)
    online = [w for w in (workers or []) if w.get("online")]
    remote_online = [
        w
        for w in online
        if not w.get("is_self") and str(w.get("worker_id") or "") != self_worker_id
    ]
    blender_targets = pick_blender_workers(
        online, prefer_remote=True, self_worker_id=self_worker_id
    )
    advice = []
    if classification.get("mesh") and not remote_online and classification.get("kind") != "none":
        advice.append("mesh-worthy intent but no remote workers online — will stay local/host")
    if classification.get("kind") == "blender_render" and not blender_targets:
        advice.append("no online worker reports capabilities.blender")
    if classification.get("kind") == "blender_render" and len(blender_targets) >= 2:
        advice.append(
            f"ready to shard across {len(blender_targets)} blender workers via workers.blender-shard"
        )
    return {
        "success": True,
        "classification": classification,
        "online_workers": len(online),
        "online_remote": len(remote_online),
        "blender_workers": [w.get("worker_id") for w in blender_targets],
        "advice": advice,
        "next": (
            "workers.blender-shard"
            if classification.get("kind") == "blender_render" and blender_targets
            else "workers.submit"
            if classification.get("mesh")
            else "none"
        ),
    }


def auto_mesh_enabled() -> bool:
    try:
        from managers.settings_manager import get_settings_manager

        block = get_settings_manager().get_setting("device_workers") or {}
        if isinstance(block, dict) and "auto_mesh" in block:
            return bool(block.get("auto_mesh"))
    except Exception:
        pass
    return False
