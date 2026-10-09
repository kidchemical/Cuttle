"""Job kind + status vocabulary for the unified Jobs surface.

One owned place that says what a "job" is across sources: local agent runs
(agent_events), mesh jobs (device_workers), and the in-memory executing set
(active_executions). Everything else in the jobs slice reads this; the page
never hard-codes status strings.
"""

# Unified status vocabulary (display-normalized; source rows keep their own).
STATUS_LABELS = {
    "queued": "queued",
    "claimed": "claimed",
    "starting": "starting",
    "running": "running",
    "ok": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "expired": "expired",
}

# Statuses that mean "work is not done yet" for the running-now view / badge.
ACTIVE_STATUSES = frozenset({"queued", "claimed", "starting", "running"})

# Source statuses that count as active for mesh rows.
MESH_ACTIVE_STATUSES = frozenset({"queued", "claimed", "running"})


def normalize_status(raw) -> str:
    """Map any source status onto the unified vocabulary."""
    return STATUS_LABELS.get(str(raw or ""), str(raw or "unknown"))


def is_active(status: str) -> bool:
    return normalize_status(status) in ACTIVE_STATUSES


def job_actions(kind: str, status: str) -> list:
    """Control actions a job exposes; catalog is the single source of truth."""
    actions = []
    st = normalize_status(status)
    if kind == "run":
        if st == "running":
            actions.append("cancel")
    elif kind == "mesh":
        if st in MESH_ACTIVE_STATUSES:
            actions.append("cancel")
        if st in ("succeeded", "failed", "cancelled", "expired"):
            actions.append("rerun")
    return actions