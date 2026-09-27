"""Queue TTL, attempt budgets, and reclaim policy for mesh jobs."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Seconds a job may sit queued before auto-cancel (type defaults).
DEFAULT_QUEUE_TTL_SECONDS: Dict[str, int] = {
    "ping": 5 * 60,
    "shell": 30 * 60,
    "execute_shell": 10 * 60,  # legacy alias
    "execute_shell_unsafe": 10 * 60,
    "execute_shell_ssh": 10 * 60,
    "file_copy": 2 * 3600,
    "blender_render": 12 * 3600,
    "cuttle_self_update": 45 * 60,
}

# Max claim attempts before cancel on next lease expiry (1 = no requeue).
DEFAULT_MAX_ATTEMPTS: Dict[str, int] = {
    "ping": 3,
    "shell": 2,
    "execute_shell": 1,
    "execute_shell_unsafe": 1,
    "execute_shell_ssh": 1,
    "file_copy": 2,
    # Extra room for shrink-and-retry after partial durable progress.
    "blender_render": 5,
    "cuttle_self_update": 1,
}

UNSAFE_SHELL_TYPES = frozenset(
    {"execute_shell", "execute_shell_unsafe", "execute_shell_ssh"}
)

_FALLBACK_TTL = 6 * 3600
_FALLBACK_MAX_ATTEMPTS = 2


def _settings_block() -> Dict[str, Any]:
    try:
        from managers.settings_manager import get_settings_manager

        block = get_settings_manager().get_setting("device_workers") or {}
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def normalize_job_type(job_type: str) -> str:
    t = (job_type or "").strip()
    if t == "execute_shell":
        return "execute_shell_unsafe"
    return t


def is_unsafe_shell_type(job_type: str) -> bool:
    return normalize_job_type(job_type) in {
        "execute_shell_unsafe",
        "execute_shell_ssh",
    } or (job_type or "").strip() in UNSAFE_SHELL_TYPES


def default_queue_ttl_seconds(job_type: str) -> int:
    """Return configured or built-in queue TTL for a job type."""
    kind = normalize_job_type(job_type)
    block = _settings_block()
    overrides = block.get("queue_ttl_seconds")
    if isinstance(overrides, dict) and kind in overrides:
        try:
            return max(30, int(overrides[kind]))
        except (TypeError, ValueError):
            pass
    if kind in DEFAULT_QUEUE_TTL_SECONDS:
        return DEFAULT_QUEUE_TTL_SECONDS[kind]
    return int(block.get("default_queue_ttl_seconds") or _FALLBACK_TTL)


def default_max_attempts(job_type: str) -> int:
    kind = normalize_job_type(job_type)
    block = _settings_block()
    overrides = block.get("max_attempts")
    if isinstance(overrides, dict) and kind in overrides:
        try:
            return max(1, min(int(overrides[kind]), 10))
        except (TypeError, ValueError):
            pass
    if kind in DEFAULT_MAX_ATTEMPTS:
        return DEFAULT_MAX_ATTEMPTS[kind]
    return int(block.get("default_max_attempts") or _FALLBACK_MAX_ATTEMPTS)


def requeue_on_lease_expiry(job_type: str) -> bool:
    """Unsafe shell never requeues; others may if attempts remain."""
    if is_unsafe_shell_type(job_type):
        return False
    block = _settings_block()
    never = block.get("no_requeue_types")
    kind = normalize_job_type(job_type)
    if isinstance(never, list) and kind in {str(x).strip() for x in never}:
        return False
    return True


def resolve_expires_at(
    *,
    job_type: str,
    created_at: float,
    ttl_seconds: Optional[float] = None,
    expires_at: Optional[float] = None,
) -> float:
    """Compute absolute expires_at for a new queued job."""
    if expires_at is not None:
        try:
            exp = float(expires_at)
            if exp > 0:
                return exp
        except (TypeError, ValueError):
            pass
    ttl = None
    if ttl_seconds is not None:
        try:
            ttl = float(ttl_seconds)
        except (TypeError, ValueError):
            ttl = None
    if ttl is None or ttl <= 0:
        ttl = float(default_queue_ttl_seconds(job_type))
    return float(created_at) + max(30.0, ttl)


def resolve_max_attempts(
    *,
    job_type: str,
    max_attempts: Optional[int] = None,
) -> int:
    if max_attempts is not None:
        try:
            return max(1, min(int(max_attempts), 10))
        except (TypeError, ValueError):
            pass
    return default_max_attempts(job_type)


def reclaim_disposition(
    *,
    job_type: str,
    attempts: int,
    max_attempts: int,
) -> Tuple[str, str]:
    """
    Decide what to do when a claim lease expires.

    Returns (action, reason) where action is 'requeue' or 'cancel'.
    """
    kind = normalize_job_type(job_type) or job_type or "job"
    used = max(0, int(attempts or 0))
    cap = max(1, int(max_attempts or 1))
    if not requeue_on_lease_expiry(job_type):
        return "cancel", f"lease expired — {kind} does not requeue"
    if used >= cap:
        return "cancel", f"lease expired — max attempts ({cap}) reached"
    return "requeue", f"lease expired — attempt {used}/{cap}"


def fail_disposition(
    *,
    job_type: str,
    attempts: int,
    max_attempts: int,
    retry: bool,
) -> Tuple[str, str]:
    """
    Decide what to do when a worker reports failure.

    Returns (action, reason) where action is 'requeue' or 'fail'.
    Mirrors lease reclaim attempt budgets so retry=True cannot loop forever.
    """
    kind = normalize_job_type(job_type) or job_type or "job"
    used = max(0, int(attempts or 0))
    cap = max(1, int(max_attempts or 1))
    if not retry:
        return "fail", f"{kind} permanent failure"
    if not requeue_on_lease_expiry(job_type):
        return "fail", f"{kind} does not requeue"
    if used >= cap:
        return "fail", f"max attempts ({cap}) reached"
    return "requeue", f"retryable failure — attempt {used}/{cap}"


# Soft (idle) + hard (absolute) defaults — generic by job type, not engine SPF.
# idle <= 0 means disabled (progress_mode typically "alive"; hard is the cap).
DEFAULT_IDLE_TIMEOUT_SECONDS: Dict[str, int] = {
    "ping": 0,
    "shell": 0,
    "execute_shell_unsafe": 0,
    "execute_shell_ssh": 0,
    "file_copy": 0,
    # Durable units (frames) must keep advancing; stuck blender = idle kill + gap-fill.
    "blender_render": 45 * 60,
    "cuttle_self_update": 0,
}

DEFAULT_HARD_TIMEOUT_SECONDS: Dict[str, int] = {
    "ping": 5 * 60,
    "shell": 4 * 3600,
    "execute_shell_unsafe": 4 * 3600,
    "execute_shell_ssh": 4 * 3600,
    "file_copy": 2 * 3600,
    # Absolute safety net only — soft idle + progress keep long Cycles alive.
    "blender_render": 24 * 3600,
    "cuttle_self_update": 30 * 60,
}

_FALLBACK_IDLE = 0
_FALLBACK_HARD = 4 * 3600


def default_idle_timeout_seconds(job_type: str) -> int:
    kind = normalize_job_type(job_type)
    block = _settings_block()
    overrides = block.get("idle_timeout_seconds")
    if isinstance(overrides, dict) and kind in overrides:
        try:
            return max(0, int(overrides[kind]))
        except (TypeError, ValueError):
            pass
    if kind in DEFAULT_IDLE_TIMEOUT_SECONDS:
        return DEFAULT_IDLE_TIMEOUT_SECONDS[kind]
    return int(block.get("default_idle_timeout_seconds") or _FALLBACK_IDLE)


def default_hard_timeout_seconds(job_type: str) -> int:
    kind = normalize_job_type(job_type)
    block = _settings_block()
    overrides = block.get("hard_timeout_seconds")
    if isinstance(overrides, dict) and kind in overrides:
        try:
            return max(30, int(overrides[kind]))
        except (TypeError, ValueError):
            pass
    if kind in DEFAULT_HARD_TIMEOUT_SECONDS:
        return DEFAULT_HARD_TIMEOUT_SECONDS[kind]
    return int(block.get("default_hard_timeout_seconds") or _FALLBACK_HARD)


def resolve_timeouts(
    *,
    job_type: str,
    params: Optional[Dict[str, Any]] = None,
    units: int = 0,
) -> Tuple[int, int, str]:
    """
    Resolve (idle_timeout, hard_timeout, progress_mode) for a job.

    Explicit ``params.timeout_seconds`` raises the hard cap (never lowers below
    type default unless ``params.hard_timeout_seconds`` is set).
    ``params.idle_timeout_seconds`` / ``params.progress_mode`` override defaults.
    """
    p = params if isinstance(params, dict) else {}
    kind = normalize_job_type(job_type)
    idle = default_idle_timeout_seconds(kind)
    hard = default_hard_timeout_seconds(kind)
    mode = "units" if kind == "blender_render" else "alive"

    if p.get("idle_timeout_seconds") is not None:
        try:
            idle = max(0, int(p.get("idle_timeout_seconds")))
        except (TypeError, ValueError):
            pass
    if p.get("hard_timeout_seconds") is not None:
        try:
            hard = max(30, int(p.get("hard_timeout_seconds")))
        except (TypeError, ValueError):
            pass
    elif p.get("timeout_seconds") is not None:
        # Legacy override: treat as hard floor (at least this long).
        try:
            hard = max(hard, max(30, int(p.get("timeout_seconds"))))
        except (TypeError, ValueError):
            pass

    # Optional generic hint: secs_per_unit * units (no engine names).
    hint = p.get("secs_per_unit_hint")
    try:
        n = max(0, int(units or 0))
        spu = float(hint) if hint is not None else 0.0
        if n > 0 and spu > 0:
            hard = max(hard, int(n * spu) + int(idle or 0))
    except (TypeError, ValueError):
        pass

    pm = str(p.get("progress_mode") or "").strip().lower()
    if pm in ("output", "alive", "units"):
        mode = pm

    return int(idle), int(hard), mode
